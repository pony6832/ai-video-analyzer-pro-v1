import json
from pathlib import Path

from pydantic import BaseModel

from app.config import PROJECT_ROOT


PROFILES_PATH = PROJECT_ROOT / "data" / "gem_profiles.json"


class GemProfile(BaseModel):
    id: str
    name: str
    instructions: str


def load_profiles() -> list[GemProfile]:
    if not PROFILES_PATH.exists():
        return []
    return [GemProfile.model_validate(item) for item in json.loads(PROFILES_PATH.read_text(encoding="utf-8"))]


def get_profile(profile_id: str) -> GemProfile:
    profiles = load_profiles()
    for profile in profiles:
        if profile.id == profile_id:
            return profile
    if profiles:
        return profiles[0]
    return GemProfile(
        id="production_director",
        name="影視後製導演與內容企劃",
        instructions="你是一位資深的影視後製導演與內容企劃，請擷取訪談逐字稿中的核心摘要、Golden Quotes、章節結構、剪輯建議與 Timecode。",
    )
