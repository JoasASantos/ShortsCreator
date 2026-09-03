"""Curated catalog of fish.audio voices, grouped by use.

The raw fish.audio catalog holds thousands of voices with repeated names and
uneven quality. This list is a vetted selection: every entry was looked up in
the real catalog, speaks Portuguese and carries a like count showing the
community approved of it. Installing a preset only creates the local voice
pointing at the `reference_id` — nothing is copied or hosted here.
"""
from __future__ import annotations

# category -> list of voices. `id` is the fish.audio reference_id.
PRESETS: dict[str, dict] = {
    "narracao": {
        "label": "Narração e documentário",
        "hint": "Vozes de locução para cinema, história e conteúdo sério",
        "voices": [
            {"id": "df1fa6d2ae194b3ebcbae60df48fde35", "name": "Narrador de propagandas",
             "note": "locução comercial, projeção forte", "likes": 740},
            {"id": "0ba1afd27db44eb2b4cb27fd331b93aa", "name": "Narrador de histórias e ciências",
             "note": "didático, bom para explicação", "likes": 325},
            {"id": "3cd7afcc61e34d6c8aef448350d9e94e", "name": "Narrador profissional",
             "note": "neutro, uso geral", "likes": 114},
            {"id": "e2b7e279f9ee47138bdf0b6d62356e8b", "name": "Narrador oficial",
             "note": "formal, institucional", "likes": 111},
            {"id": "117c2627e1614b3dbfe72d3675ec5841", "name": "Documentário natureza",
             "note": "cadência de documentário", "likes": 2},
        ],
    },
    "cinema": {
        "label": "Cinema e trailer",
        "hint": "Tom de trailer, crítica de filme e resumo de série",
        "voices": [
            {"id": "4b215a58407c4def88e0892def6421b2", "name": "Narrador Todo Mundo Odeia o Chris",
             "note": "narração cômica em primeira pessoa", "likes": 640},
            {"id": "5497e90cdf4440ce9a92b5a5cd393422", "name": "Narrador de trailer de filme",
             "note": "grave, dramático", "likes": 4},
            {"id": "22b6b157a1a84f099a9fddddc7249313", "name": "Narrador de trailer e filmes",
             "note": "anúncio de estreia", "likes": 2},
            {"id": "9fe4f4fa709f4869ab5b681eadbebae0", "name": "Documentário de crime",
             "note": "true crime, tensão", "likes": 1},
        ],
    },
    "personagens": {
        "label": "Personagens",
        "hint": "Vozes de personagens de animação e anime",
        "voices": [
            {"id": "507bee08fbcd4180b7ae1c82ccb94bf5", "name": "Seishiro Nagi",
             "note": "Blue Lock, apático e arrastado", "likes": 128},
            {"id": "4a199911b26a49daace0a82491f43055", "name": "Nagi",
             "note": "variação da voz do Nagi", "likes": 108},
            {"id": "41ee86659d5d4c50a54950ff66d13b36", "name": "Eric Cartman",
             "note": "South Park, dublagem br", "likes": 265},
            {"id": "0784aeee9fb14ef8a7d1e50e45809ebf", "name": "Eric Cartman (South Park BR)",
             "note": "variação do Cartman", "likes": 23},
            {"id": "bdb4986093f8403d8dad0858c0628aa1", "name": "Rick Sanchez (1ª temporada)",
             "note": "Rick and Morty, rouco e sarcástico", "likes": 283},
            {"id": "9318b32b5477496a960e2958dd51c7ea", "name": "Rick Sanchez",
             "note": "variação do Rick", "likes": 182},
            {"id": "6a88fe5df52d49d3a063241edc9f32b9", "name": "Peter Griffin",
             "note": "Family Guy, dublagem br", "likes": 143},
        ],
    },
    "geral": {
        "label": "Vozes gerais pt-BR",
        "hint": "Locução limpa para tecnologia, negócios e curiosidades",
        "voices": [
            {"id": "04736e4d6a644abab81e601a7d2ae4b9", "name": "Waldo Morais",
             "note": "masculina, natural", "likes": 1834},
            {"id": "5661bf8cb97740fcb10d2f756abf7779", "name": "Isabela",
             "note": "feminina, clara", "likes": 656},
        ],
    },
}


def all_presets() -> list[dict]:
    """Flatten the catalog for the UI, keeping the category on each item."""
    out: list[dict] = []
    for key, group in PRESETS.items():
        for voice in group["voices"]:
            out.append({
                "category": key,
                "category_label": group["label"],
                "category_hint": group["hint"],
                **voice,
            })
    return out


def find(reference_id: str) -> dict | None:
    for voice in all_presets():
        if voice["id"] == reference_id:
            return voice
    return None
