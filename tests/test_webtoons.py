import asyncio
import time

import mandown


async def main() -> None:
    for query in ["shadow slave"]:
        print(f"\n=== {query} ===")
        start = time.perf_counter()
        async for source, groups in mandown.search_all(query, deduplicate=True):
            elapsed = time.perf_counter() - start
            print(f"\n[{source}] {len(groups)} ayrı grup ({elapsed:.2f}s)")
            for index, group in enumerate(groups, 1):
                print(f"\n  Grup {index}")
                print(f"    Başlıklar: {' + '.join(group.titles)}")
                print(f"    Kaynaklar: {', '.join(group.sources)}")
                for group_source, url in group.urls.items():
                    if url is not None:
                        print(f"    {group_source}: {url}")


if __name__ == "__main__":
    asyncio.run(main())


"""
url = (
    "https://m.comic.naver.com/webtoon/list?titleId=746857&week=thu&sortOrder=ASC"
)

comic = mandown.query(url)

print("Title:", comic.metadata.title)
print("Chapter count:", len(comic.chapters))
main_progress, chapter_progress = None, None

def process(main_progress, chapter_progress):
    os.system("cls")
    print(f"Ana ilerleme : {main_progress.progress}")
    print(f"Bölüm ilerlemesi : {chapter_progress.progress}")
if __name__ == '__main__':
        comic = mandown.query(url)
        main_progress, chapter_progress, thread = mandown.download(
            comic,
            start=200,
            end=201,
            threads=4,
            only_download_missing=False,
            progress_callback=process,
            panel_size=(1200, 800),
            image_format="jpg",
        )
"""
