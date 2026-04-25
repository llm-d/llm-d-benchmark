"""Utility functions for the result store."""

def color_pad(text: str, width: int, color_code: str = "31") -> str:
    """Pads text to a given width, handling ANSI color codes for 'missing' and '#'."""
    colored_text = str(text)
    if "missing" in colored_text:
        colored_text = colored_text.replace("missing", f"\033[{color_code}mmissing\033[0m")
    if "#" in colored_text:
        colored_text = colored_text.replace("#", f"\033[{color_code}m#\033[0m")
        
    if colored_text != str(text):
        # If we added color codes, we need to calculate padding based on original text length
        return colored_text + " " * (width - len(str(text)))
    return f"{str(text):<{width}}"

def parse_report_path(relative_name: str) -> dict:
    """Parses a relative GCS path into run metadata."""
    parts = relative_name.split("/")
    if len(parts) >= 6:
        # Format: group/scenario/model.../hardware/run_uid/report_v0.2.yaml
        group = parts[0]
        scenario = parts[1]
        model = "/".join(parts[2:-3])
        hardware = parts[-3]
        run_uid = parts[-2]
        return {
            "run_uid": run_uid,
            "scenario": scenario,
            "model": model,
            "hardware": hardware,
            "group": group,
        }
    return None
