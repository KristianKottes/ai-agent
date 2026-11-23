import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./services/api";

export function AvatarVideo({ renderId, label }: { renderId: string; label: string }) {
  const [url, setUrl] = useState<string>();
  const [failedUrl, setFailedUrl] = useState<string>();
  const media = useQuery({
    queryKey: ["avatar-video", renderId],
    queryFn: () => api.avatarVideo(renderId),
    staleTime: Infinity,
    gcTime: 0,
  });

  useEffect(() => {
    if (!media.data) return;
    const source = URL.createObjectURL(new Blob([media.data], { type: "video/mp4" }));
    setUrl(source);
    return () => URL.revokeObjectURL(source);
  }, [media.data]);

  if (media.isFetching) return <p role="status">Loading video…</p>;
  if (media.isError) return <>
    <p className="settings-error" role="alert">{media.error.message}</p>
    <button type="button" className="secondary-button" onClick={() => media.refetch()}>Try loading again</button>
  </>;
  if (!url) return null;

  return <>
    <video src={url} controls playsInline aria-label={label}
      onCanPlay={() => setFailedUrl(undefined)} onError={() => setFailedUrl(url)} />
    {failedUrl === url && <p className="settings-error" role="alert">
      The preview couldn’t play this clip. Open the saved MP4 at the path above.
    </p>}
  </>;
}
