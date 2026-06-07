import mandown

url = "https://mangadex.org/title/be5a9292-79c6-4db1-8a30-9e5e6b3d8f64/futsuu-to-bakemono"

comic = mandown.query(url)

print("Title:", comic.metadata.title)
print("Chapter count:", len(comic.chapters))

mandown.download(comic, "./downloads")