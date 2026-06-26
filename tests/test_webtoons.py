import mandown, time, os

url = "https://mangadex.org/chapter/9efed0b1-2448-4f4e-b898-319eee003a83"

comic = mandown.query(url)

print("Title:", comic.metadata.title)
print("Chapter count:", len(comic.chapters))
main_progress, chapter_progress = None, None

def process(main_progress, chapter_progress):
    os.system("clear")
    print(f"Ana ilerleme : {main_progress.progress}")
    print(f"Bölüm ilerlemesi : {chapter_progress.progress}")

main_progress, chapter_progress, thread = mandown.download(comic, threads=10, only_download_missing=False)

