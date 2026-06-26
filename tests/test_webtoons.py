import mandown, time, os

url = "https://mangadex.org/title/5b356a49-627a-498f-9b67-78eae9d8c3d9/mahouka-koukou-no-rettousei-yotsuba-keishou-hen-movie-promotion"

comic = mandown.query(url)

print("Title:", comic.metadata.title)
print("Chapter count:", len(comic.chapters))
main_progress, chapter_progress = None, None

def process(main_progress, chapter_progress):
    os.system("cls")
    print(f"Ana ilerleme : {main_progress.progress}")
    print(f"Bölüm ilerlemesi : {chapter_progress.progress}")
if __name__ == '__main__':
    main_progress, chapter_progress, thread = mandown.download(comic, threads=4, only_download_missing=True, progress_callback=process)

