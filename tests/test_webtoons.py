import mandown

url = "https://www.webtoons.com/en/fantasy/surviving-the-game-as-a-barbarian/list?title_no=5515"

comic = mandown.query(url)

print("Title:", comic.metadata.title)
print("Chapter count:", len(comic.chapters))

mandown.download(comic, "./downloads")