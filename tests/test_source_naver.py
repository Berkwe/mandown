from common import is_source_working, skip_in_ci


MUSA_DESCRIPTION = (
    "[ 2025 오늘의 우리만화 수상작 ]\n\n"
    "2세기 후반, 마한연맹 고리국(古離國)의 무사 나루.\n"
    "믿었던 스승의 배신으로 나라가 멸망하고,\n"
    "평생을 바쳐 지켜주겠노라 맹세했던 소단공주의 행방이 묘연해진다.\n\n"
    "소단 공주의 행방을 쫓던 나루는\n"
    "서역으로 떠났다는 배신자의 말에 로마제국의 검투 노예로 팔려가 처절한 사투를 벌이는데..."
)


@skip_in_ci
def test_musa_mallihaeng() -> None:
    return is_source_working(
        "https://m.comic.naver.com/webtoon/list?titleId=746857&week=thu&sortOrder=ASC",
        title="무사만리행",
        authors=["운", "배민기"],
        genres=["무협/사극", "성장물", "퓨전사극", "판무", "먼치킨"],
        description=MUSA_DESCRIPTION,
        cover_art=(
            "https://image-comic.pstatic.net/webtoon/746857/thumbnail/"
            "thumbnail_IMAG21_9a0d4005-34a6-4fb5-a9dc-61a305cb580d.jpg"
        ),
        refined_url="https://m.comic.naver.com/webtoon/list?titleId=746857&sortOrder=ASC",
    )
