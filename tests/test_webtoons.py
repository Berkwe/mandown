import mandown, time, os


search = mandown.search("Star Emb")
while True:
    a = input(" : ")
    print(eval(a))
    


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