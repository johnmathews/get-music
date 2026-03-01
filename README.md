This project will be used to add music to Navidrome.

Navidrome runs on an LXC on my proxmox server. You can ssh into the LXC by doing `ssh music` if on the home network or
`ssh musict` if outside the homenetwork.

Files for Navidrome are stored at `/mnt/nfs/music/` on the LXC. this is an NFS mount to a trueNAS dataset called `music`
that exists in the `tank` datapool on an HDD.

This tool will be called `gm` (for get music) and will accept either a directory, a filename, or a youtube url. If it is
a directory it will process all music files in the directory. If it is a youtube url it will use yt-dlp to download the
audio and the artwork and metadata.

Check if navidrome supports playing audio from video files - if it does then the `gm` tool can download the movie from
youtube, but if navidrome does not then the `gm` tool should only download the audio and metadata from youtube. The same
logic applies to files passed to the tool.

Ask questions if you need to.

Music files should be stored on the music dataset at /mnt/nfs/music in the following directory structure:

Artist > Album > Song

If a directory is passed to `gm` then the tool should ask if it should search recursively.

The tool can be written in python, but needs a shell wrapper so that i can call the tool from a terminal console.

Example usage:

- `gm <youtube-url>`

- `gm <directory>`

This project should include documentation for humans as well as agents like yourself.

The tool should have a help section. e.g. `gm help`.
