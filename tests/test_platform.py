from __future__ import annotations

from backend.platform import linux, macos, windows
from backend.platform.detect import collect_system_report
from backend.platform.models import AcceleratorInfo
from backend.platform.paths import APP_ID, application_directories


def test_linux_memory_and_pci_parsers():
    total, available = linux.parse_meminfo(
        "MemTotal:       16384000 kB\nMemAvailable:    8123456 kB\nMemFree: 100 kB\n"
    )
    assert total == 16_777_216_000
    assert available == 8_318_418_944

    inventory = linux.parse_lspci(
        "0000:01:00.0 VGA compatible controller [0300]: NVIDIA Corporation AD104 [GeForce RTX 4070] [10de:2786]\n"
        "0000:00:14.0 USB controller [0c03]: Intel Corporation Device [8086:7a60]"
    )
    assert inventory["0000:01:00.0"]["name"] == "NVIDIA Corporation AD104 [GeForce RTX 4070]"
    assert inventory["0000:01:00.0"]["vendor_id"] == "10de"
    assert "0000:00:14.0" not in inventory


def test_macos_vm_stat_parser_uses_reclaimable_pages():
    memory, method = macos.parse_available_memory(
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free: 100.\nPages inactive: 200.\nPages speculative: 10.\nPages purgeable: 5.\n"
    )
    assert memory == 315 * 16384
    assert method and "inactive" in method


def test_windows_adapter_normalizes_cim_and_memory(monkeypatch):
    cuda = AcceleratorInfo(name="CUDA", detection_status="unknown", evidence="Not queried in test")
    monkeypatch.setattr(windows, "_global_memory_status", lambda: {"total": 32, "available": 12})
    monkeypatch.setattr(
        windows,
        "_cim_inventory",
        lambda: {
            "cpu_model": "Test CPU",
            "gpus": [{"Name": "Integrated Graphics", "AdapterCompatibility": "Test Vendor", "DriverVersion": "1.2"}],
        },
    )
    monkeypatch.setattr(windows, "nvidia_inventory", lambda: ([], cuda))

    report = windows.collect()
    assert report["cpu_model"] == "Test CPU"
    assert report["memory_total_bytes"] == 32
    assert report["gpus"][0].name == "Integrated Graphics"
    assert report["gpus"][0].driver_version == "1.2"
    assert next(item for item in report["accelerators"] if item.name == "Metal").detection_status == "not_detected"


def test_tauri_paths_override_native_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_BLOGGER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AI_BLOGGER_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AI_BLOGGER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("AI_BLOGGER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("AI_BLOGGER_TEMP_DIR", str(tmp_path / "temp"))
    paths = application_directories()
    assert paths.data == tmp_path / "data"
    assert paths.cache == tmp_path / "cache"
    assert paths.config == tmp_path / "config"
    assert paths.logs == tmp_path / "logs"
    assert paths.temp == tmp_path / "temp"


def test_native_directory_defaults_for_each_platform(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.platform.paths.Path.home", lambda: tmp_path)
    for name in ("AI_BLOGGER_DATA_DIR", "AI_BLOGGER_CACHE_DIR", "AI_BLOGGER_CONFIG_DIR", "AI_BLOGGER_LOG_DIR", "AI_BLOGGER_TEMP_DIR",
                 "LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr("backend.platform.paths.platform.system", lambda: "Darwin")
    macos_paths = application_directories()
    support = tmp_path / "Library" / "Application Support" / APP_ID
    assert macos_paths.data == support
    assert macos_paths.cache == tmp_path / "Library" / "Caches" / APP_ID
    assert macos_paths.logs == tmp_path / "Library" / "Logs" / APP_ID
    assert macos_paths.temp == macos_paths.cache / "tmp"

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr("backend.platform.paths.platform.system", lambda: "Windows")
    windows_paths = application_directories()
    assert windows_paths.data == tmp_path / "Local" / APP_ID
    assert windows_paths.config == tmp_path / "Roaming" / APP_ID
    assert windows_paths.temp == windows_paths.cache / "tmp"

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setattr("backend.platform.paths.platform.system", lambda: "Linux")
    linux_paths = application_directories()
    assert linux_paths.data == tmp_path / "xdg-data" / APP_ID
    assert linux_paths.cache == tmp_path / "xdg-cache" / APP_ID
    assert linux_paths.config == tmp_path / "xdg-config" / APP_ID
    assert linux_paths.temp == linux_paths.cache / "tmp"


def test_windows_missing_memory_probe_does_not_claim_availability(monkeypatch):
    monkeypatch.setattr(windows, "_global_memory_status", lambda: None)
    monkeypatch.setattr(windows, "_cim_inventory", lambda: {})
    monkeypatch.setattr(windows, "nvidia_inventory", lambda: ([], AcceleratorInfo(name="CUDA", detection_status="unknown", evidence="Not queried")))
    report = windows.collect()
    assert report["memory_available_bytes"] is None
    assert report["memory_available_method"] is None


def test_system_report_contains_host_observations(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_BLOGGER_DATA_DIR", str(tmp_path))
    report = collect_system_report()
    assert report.os_name in {"macOS", "Windows", "Linux"}
    assert report.architecture != "unknown"
    assert report.logical_cpu_count and report.logical_cpu_count > 0
    assert report.storage.available_bytes is not None
    assert report.storage.data_directory == str(tmp_path)
    assert all(item.inference_compatibility == "not_tested" for item in report.accelerators)
