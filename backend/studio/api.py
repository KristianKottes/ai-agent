"""Authenticated routes for the project audio studios."""

from typing import List, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import Field
from typing_extensions import Annotated

from backend.studio import illustrated, images, topic_library, video
from backend.studio.audio import StudioError, analyze
from backend.studio.contracts import CompositionCopy, Draft, MediaInput, PresetInput, PreviewRequest, RecordingInput, ReferenceInput, RenderRequest, ToolAction
from backend.studio.speech import SpeechRequest, TextRequest
from backend.studio.text import mixed_language, normalize
from backend.studio import pronunciation
from backend.studio import morning_defaults
from backend.voice.service import VoiceError
from backend.ai.mamay import MamaySettings, SettingsConflict, load_settings, save_settings


class StudioRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except (StudioError, VoiceError) as error:
                raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        return handle


def router(token):
    api = APIRouter(prefix="/api/studio", route_class=StudioRoute, dependencies=[Depends(token)])

    def studio(request: Request):
        return request.app.state.studio

    @api.get("/tools")
    def tools(request: Request):
        return studio(request).tools.snapshot()

    @api.post("/tools/{component}")
    def tool_action(component: str, payload: ToolAction, request: Request):
        return studio(request).tools.action(component, payload)

    @api.post("/normalize")
    def normalized(payload: TextRequest):
        return {"text": normalize(payload.text, payload.language, payload.pronunciation),
                "mixed_language": mixed_language(payload.text)}

    @api.get("/presets")
    def presets(request: Request):
        return studio(request).presets()

    @api.post("/presets", status_code=201)
    def preset_save(payload: PresetInput, request: Request):
        return studio(request).save_preset(payload)

    @api.delete("/presets/{preset_id}")
    def preset_delete(preset_id: UUID, request: Request):
        return studio(request).delete_preset(str(preset_id))

    @api.get("/profiles/{profile}/reference")
    def reference_path(profile: UUID, request: Request):
        return {"path": str(studio(request).reference_path(str(profile)))}

    @api.delete("/profiles/{profile}")
    def profile_delete(profile: UUID, request: Request):
        return studio(request).delete_profile(str(profile))

    @api.get("/{project}/workspace")
    def workspace(project: UUID, request: Request):
        key = str(project)
        service = studio(request)
        return {"draft": service.draft(key), "assets": service.assets(key), "jobs": service.jobs(key),
                "profiles": service.profiles(key)}

    @api.put("/{project}/draft")
    def draft_save(project: UUID, payload: Draft, request: Request):
        return studio(request).save_draft(str(project), payload)

    @api.delete("/{project}/draft")
    def draft_delete(project: UUID, request: Request):
        return studio(request).clear_draft(str(project))

    @api.post("/{project}/copy", status_code=201)
    def copy_composition(project: UUID, payload: CompositionCopy, request: Request):
        title = payload.title.strip()
        if not title:
            raise StudioError("Name the copied visualizer project.")
        draft = Draft.model_validate(payload.model_dump(exclude={"title"}))
        return studio(request).copy_composition(str(project), title, draft)

    @api.post("/{project}/speech", status_code=202)
    def speak(project: UUID, payload: Annotated[List[SpeechRequest], Field(min_length=1, max_length=16)], request: Request):
        return studio(request).enqueue(str(project), "speech", payload)

    @api.post("/{project}/record")
    def record(project: UUID, payload: RecordingInput, request: Request):
        return studio(request).record(str(project), payload.seconds)

    @api.post("/{project}/profiles", status_code=201)
    def profile_save(project: UUID, payload: ReferenceInput, request: Request):
        return studio(request).create_profile(str(project), payload)

    @api.put("/{project}/profiles/{profile}/{assignment}")
    def profile_assign(project: UUID, profile: UUID, assignment: Literal["assign", "unassign"], request: Request):
        return studio(request).assign_profile(str(project), str(profile), assignment == "assign")

    @api.post("/{project}/import/{kind}", status_code=201)
    def asset_import(project: UUID, kind: Literal["audio", "image", "background"], payload: MediaInput, request: Request):
        return studio(request).import_media(str(project), kind, payload)

    @api.post("/{project}/narration/{narration}", status_code=201)
    def adopt_narration(project: UUID, narration: UUID, request: Request):
        return studio(request).adopt_narration(str(project), str(narration))

    @api.get("/{project}/assets/{asset}/analysis")
    def analysis(project: UUID, asset: UUID, request: Request):
        service = studio(request)
        service.tools.require("audio-analysis", "ffmpeg")
        return analyze(service.asset_path(str(project), str(asset), "audio")).summary()

    @api.get("/{project}/assets/{asset}/file/{format}")
    def media_path(project: UUID, asset: UUID, format: Literal["original", "mp3", "wav", "thumbnail"], request: Request):
        service = studio(request)
        if format == "thumbnail":
            return {"path": str(service.image_thumbnail(str(project), str(asset)))}
        path = (service.asset_path(str(project), str(asset)) if format == "original"
                else service.download_audio(str(project), str(asset), format))
        return {"path": str(path)}

    @api.get("/{project}/assets/{asset}/subtitles/{format}")
    def captions(project: UUID, asset: UUID, format: Literal["srt", "ass", "vtt"], request: Request):
        service = studio(request)
        record = service.asset(str(project), str(asset))
        if record.get("source_type") == "video_creator" or format == "vtt":
            return {"text": video.subtitle_text(record.get("cues", []), format)}
        return {"text": service.subtitle_file(str(project), str(asset), format)}

    @api.post("/{project}/preview")
    def preview(project: UUID, payload: PreviewRequest, request: Request):
        return studio(request).preview(str(project), payload)

    @api.post("/{project}/render", status_code=202)
    def render(project: UUID, payload: RenderRequest, request: Request):
        return studio(request).enqueue(str(project), "render", [payload])[0]

    @api.get("/{project}/video")
    def video_timeline(project: UUID, request: Request):
        return studio(request).video_timeline(str(project))

    @api.put("/{project}/video")
    def video_save(project: UUID, payload: video.Timeline, request: Request):
        return studio(request).save_video(str(project), payload)

    @api.get("/video/encoders")
    def video_codecs():
        available = video.encoders()
        return {"video": [key for key, codec in video.VIDEO_CODECS.items() if codec in available],
                "audio": [key for key, codec in video.AUDIO_CODECS.items() if codec in available]}

    @api.post("/{project}/video/render", status_code=202)
    def video_render(project: UUID, payload: video.VideoRequest, request: Request):
        return studio(request).enqueue(str(project), "video", [payload])[0]

    @api.get("/illustrated/status")
    def illustrated_status(request: Request):
        return illustrated.readiness(studio(request))

    @api.get("/illustrated/defaults")
    def generation_defaults(request: Request):
        return morning_defaults.load_defaults(studio(request).data)

    @api.put("/illustrated/defaults")
    def generation_defaults_save(payload: morning_defaults.GenerationDefaults, request: Request):
        return morning_defaults.save_defaults(studio(request).data, payload)

    @api.get("/illustrated/writing-settings")
    def mamay_settings(request: Request):
        return {"settings": load_settings(studio(request).data), "defaults": MamaySettings()}

    @api.put("/illustrated/writing-settings")
    def mamay_settings_save(payload: MamaySettings, request: Request):
        try:
            return save_settings(studio(request).data, payload)
        except SettingsConflict as error:
            raise StudioError(str(error), 409) from error

    @api.post("/{project}/illustrated/topics", status_code=202)
    def morning_topics(project: UUID, payload: illustrated.TopicRequest, request: Request):
        return studio(request).enqueue(str(project), illustrated.TOPIC_JOB, [payload])[0]

    @api.get("/{project}/illustrated/topics")
    def saved_morning_topics(project: UUID, request: Request):
        return topic_library.list_topics(studio(request), str(project))

    @api.put("/{project}/illustrated/topics/{id}")
    def edit_morning_topic(project: UUID, id: UUID, payload: topic_library.TopicEdit, request: Request):
        return topic_library.edit_topic(studio(request), str(project), str(id), payload)

    @api.patch("/{project}/illustrated/topics/{id}")
    def flag_morning_topic(project: UUID, id: UUID, payload: topic_library.TopicFlags, request: Request):
        return topic_library.set_flags(studio(request), str(project), str(id), payload)

    @api.get("/illustrated/speech-settings")
    def speech_settings(request: Request):
        return pronunciation.load_settings(studio(request).data)

    @api.put("/illustrated/speech-settings")
    def speech_settings_save(payload: pronunciation.SpeechSettings, request: Request):
        return pronunciation.save_settings(studio(request).data, payload)

    @api.get("/{project}/illustrated/defaults")
    def project_generation_defaults(project: UUID, request: Request):
        instance = studio(request)
        instance.project(str(project))
        return morning_defaults.load_defaults(instance.data, str(project))

    @api.put("/{project}/illustrated/defaults")
    def project_generation_defaults_save(project: UUID, payload: morning_defaults.GenerationDefaults, request: Request):
        instance = studio(request)
        instance.project(str(project))
        return morning_defaults.save_defaults(instance.data, payload, str(project))

    @api.get("/{project}/illustrated/writing-settings")
    def project_writing_settings(project: UUID, request: Request):
        instance = studio(request)
        instance.project(str(project))
        return {"settings": load_settings(instance.data, str(project)), "defaults": MamaySettings()}

    @api.put("/{project}/illustrated/writing-settings")
    def project_writing_settings_save(project: UUID, payload: MamaySettings, request: Request):
        instance = studio(request)
        instance.project(str(project))
        try:
            return save_settings(instance.data, payload, str(project))
        except SettingsConflict as error:
            raise StudioError(str(error), 409) from error

    @api.get("/{project}/illustrated/speech-settings")
    def project_speech_settings(project: UUID, request: Request):
        instance = studio(request)
        instance.project(str(project))
        return pronunciation.load_settings(instance.data, str(project))

    @api.put("/{project}/illustrated/speech-settings")
    def project_speech_settings_save(project: UUID, payload: pronunciation.SpeechSettings, request: Request):
        instance = studio(request)
        instance.project(str(project))
        return pronunciation.save_settings(instance.data, payload, str(project))

    @api.get("/{project}/illustrated/narrators")
    def morning_narrators(project: UUID, request: Request):
        return illustrated.narrators(studio(request), str(project))

    @api.post("/{project}/illustrated/render", status_code=202)
    def illustrated_render(project: UUID, payload: illustrated.IllustratedRequest, request: Request):
        return studio(request).enqueue(str(project), illustrated.VIDEO_JOB, [payload])[0]

    @api.post("/{project}/illustrated/install", status_code=202)
    def illustrated_install(project: UUID, payload: illustrated.WritingModelInstall, request: Request):
        return studio(request).enqueue(str(project), illustrated.MODEL_JOB, [payload])[0]

    @api.get("/image/catalog")
    def image_catalog(request: Request):
        return studio(request).images.snapshot()

    @api.post("/image/model")
    def image_model(payload: images.ModelAction, request: Request):
        return studio(request).images.action(payload)

    @api.post("/{project}/image/generate", status_code=202)
    def images_generate(project: UUID, payload: images.ImageRequest, request: Request):
        return studio(request).enqueue(str(project), "image", [payload])[0]

    @api.patch("/{project}/image/{asset}")
    def image_details(project: UUID, asset: UUID, payload: images.ImageDetails, request: Request):
        return studio(request).update_image(str(project), str(asset), payload)

    @api.get("/{project}/image-profiles")
    def image_profiles(project: UUID, request: Request):
        return studio(request).image_profiles(str(project))

    @api.get("/{project}/image-profiles/{profile}")
    def image_profile_history(project: UUID, profile: UUID, request: Request):
        return studio(request).image_profile_history(str(project), str(profile))

    @api.post("/{project}/image-profiles")
    def image_profile_save(project: UUID, payload: images.Profile, request: Request):
        return studio(request).save_image_profile(str(project), payload)

    @api.post("/{project}/jobs/{job}/retry", status_code=202)
    def retry(project: UUID, job: UUID, request: Request):
        return studio(request).retry(str(project), str(job))

    @api.post("/{project}/jobs/{job}/cancel")
    def cancel(project: UUID, job: UUID, request: Request):
        return studio(request).cancel(str(project), str(job))

    return api
