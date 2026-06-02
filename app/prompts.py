from app.schemas import FullReport, ProductionHighlightReport, SegmentAnalysis


SEGMENT_ANALYSIS_PROMPT = """你是資深新聞編輯、影音製作人與事實查核助理。

請分析這段影片片段，輸出必須是 JSON，且必須符合指定 schema。

片段資訊：
- segment_index: {segment_index}
- start_time: {start_time}
- end_time: {end_time}

分析原則：
1. 所有內容使用繁體中文。
2. 不要編造看不到或聽不到的內容。
3. 不確定的說話者請寫 unknown。
4. 不確定、需要外部查證或可能誤導的內容，放入 risk_notes，並明確標記「需要人工確認」。
5. 重要事件、重要發言、可用畫面都要盡量標示 timestamp。
6. 分析重點是新聞價值、短影音價值、畫面可用性。
7. 只輸出 JSON，不要加 Markdown，不要加說明文字。
"""


FULL_REPORT_PROMPT = """你是資深新聞主編與短影音策略編輯。

以下是多個影片片段的 JSON 分析結果。請根據這些資料做全片總整理。
輸出必須是 JSON，且必須符合指定 schema。

要求：
1. 所有內容使用繁體中文。
2. 不要編造片段分析中沒有根據的內容。
3. 不確定、需查證、可能誤導或需人工確認的內容，放入 fact_check_items。
4. 新聞稿初稿要可供新聞工作者再編修使用。
5. 旁白稿初稿要適合影音新聞或短影音使用。
6. 短影音建議要重視起訖時間、標題、平台與理由。
7. 只輸出 JSON，不要加 Markdown，不要加說明文字。

片段分析：
{segments_json}
"""


PRODUCTION_ANALYSIS_PROMPT = """{profile_instructions}

請分析我提供的{source_type}，輸出必須是 JSON，且必須符合指定 schema。

任務：
1. 核心摘要：用三句話概括本段內容的核心訊息。
2. Golden Quotes：選出 5-8 段最具感染力、適合預告片或短影音的句子，並標註情緒語氣與用途。
3. 章節結構建議：依主題切成適合 IG Reels、YouTube Shorts、TikTok 的碎片化章節，提供章節名稱、內容大綱、推薦主標題、平台。
4. 剪輯建議：指出適合 B-roll、空景、Close-up、Jump cut、字幕強調或音效轉場的段落與理由。
5. Timecode：所有重點都要盡量標記 Timecode。

限制：
- 使用台灣繁體中文。
- 專業、精煉、直搗核心。
- 避免過度文學化廢話，以攝影與剪輯實戰價值為導向。
- 不要編造看不到、聽不到或逐字稿沒有的內容。
- 若原始資料沒有時間碼，請在 timecode_notes 標記「無原始 timecode，需人工對位」。
- 不確定或需人工判斷的內容，放入 risk_notes。
- 只輸出 JSON，不要加 Markdown，不要加說明文字。

素材資訊：
{context}
"""


def segment_prompt(segment_index: int, start_time: str, end_time: str) -> str:
    return SEGMENT_ANALYSIS_PROMPT.format(
        segment_index=segment_index,
        start_time=start_time,
        end_time=end_time,
    )


def full_report_prompt(segments_json: str) -> str:
    return FULL_REPORT_PROMPT.format(segments_json=segments_json)


def production_prompt(profile_instructions: str, source_type: str, context: str) -> str:
    return PRODUCTION_ANALYSIS_PROMPT.format(
        profile_instructions=profile_instructions,
        source_type=source_type,
        context=context,
    )


SEGMENT_SCHEMA = SegmentAnalysis.model_json_schema()
FULL_REPORT_SCHEMA = FullReport.model_json_schema()
PRODUCTION_SCHEMA = ProductionHighlightReport.model_json_schema()
