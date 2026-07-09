import mandown, time, os

url = "https://mangadex.org/title/e9c6ba84-e78a-4fa5-99f8-087ab6d31a7d/musa-mallihaeng"

comic = mandown.query(url)

print("Title:", comic.metadata.title)
print("Chapter count:", len(comic.chapters))
main_progress, chapter_progress = None, None

def process(main_progress, chapter_progress):
    os.system("cls")
    print(f"Ana ilerleme : {main_progress.progress}")
    print(f"Bölüm ilerlemesi : {chapter_progress.progress}")
if __name__ == '__main__':
    for chapter_number in (35, 46):
        comic = mandown.query(url)
        main_progress, chapter_progress, thread = mandown.download(
            comic,
            start=chapter_number,
            end=chapter_number,
            threads=4,
            only_download_missing=False,
            progress_callback=process,
            panel_size=(1200, 800),
        )
