from common import is_source_working, skip_in_ci


THE_LONG_WAY_DESCRIPTION = (
    "In the second half of the 2nd century, Naru, a warrior from the Mahan tribal "
    "union of Goguryeo, was sold as a gladiator-slave to the Roman Empire and waged "
    "a desperate struggle for freedom."
)


@skip_in_ci
def test_the_long_way_of_the_warrior() -> None:
    return is_source_working(
        "https://manhwaz.com/home/manhwaz.com/public_html/public/index.php/webtoon/"
        "the-long-way-of-the-warrior-004",
        title="The Long Way Of The Warrior",
        authors=["Ming Bae"],
        genres=["Action", "Historical", "Manhwa", "Sports", "Tragedy"],
        description=THE_LONG_WAY_DESCRIPTION,
        cover_art=(
            "https://manhwaz.com/storage/images/cover/0d974d19df5b4f981cc0aa955e152c8f.jpg"
        ),
    )
