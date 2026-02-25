#!/usr/bin/env python3
"""Convert EPUB books to audiobooks using Edge TTS."""

import argparse
import asyncio
import re
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import edge_tts

CHUNK_SIZE = 4000


def flatten_toc(toc):
    """Recursively flatten EPUB TOC into ordered list of (title, href) tuples."""
    entries = []
    for item in toc:
        if isinstance(item, tuple):
            section, children = item
            if hasattr(section, 'href') and section.href:
                entries.append((section.title, section.href))
            entries.extend(flatten_toc(children))
        elif isinstance(item, epub.Link):
            entries.append((item.title, item.href))
        elif hasattr(item, 'href') and item.href:
            entries.append((item.title, item.href))
    return entries


def split_at_anchors(soup, anchor_ids):
    """Split soup text at anchor ID boundaries. Returns dict of anchor_id -> text."""
    soup = deepcopy(soup)
    found = False
    for aid in anchor_ids:
        el = soup.find(id=aid)
        if el:
            el.insert_before(soup.new_string(f"\x00SPLIT:{aid}\x00"))
            found = True
    if not found:
        return {None: soup.get_text(separator="\n", strip=True)}
    full_text = soup.get_text(separator="\n", strip=True)
    parts = re.split(r"\x00SPLIT:([^\x00]+)\x00", full_text)
    result = {}
    if parts[0].strip():
        result[None] = parts[0].strip()
    for i in range(1, len(parts), 2):
        aid = parts[i]
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if text:
            result[aid] = text
    return result


def parse_with_toc(book, toc_entries):
    """Split chapters using NCX TOC for anchor splitting, spine for reading order."""
    parsed_toc = []
    for title, href in toc_entries:
        href = unquote(href)
        filename, anchor = href.split('#', 1) if '#' in href else (href, None)
        parsed_toc.append((title, filename, anchor))
    items = {}
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        name = item.get_name()
        items[name] = item
        parts = name.split('/')
        for i in range(1, len(parts)):
            subpath = '/'.join(parts[i:])
            if subpath not in items:
                items[subpath] = item
    toc_by_item = {}
    for title, filename, anchor in parsed_toc:
        item = items.get(filename)
        if not item:
            continue
        name = item.get_name()
        if name not in toc_by_item:
            toc_by_item[name] = []
        toc_by_item[name].append((title, anchor))
    raw_chapters = []
    for spine_id, _ in book.spine:
        item = book.get_item_with_id(spine_id)
        if not item:
            continue
        soup = BeautifulSoup(item.get_body_content(), "html.parser")
        name = item.get_name()
        if name in toc_by_item:
            entries = toc_by_item[name]
            anchors = [a for _, a in entries if a]
            if anchors:
                sections = split_at_anchors(soup, anchors)
                if None in sections:
                    pre_title = sections[None].strip().split("\n")[0][:80]
                    raw_chapters.append((pre_title, sections[None], False))
                for title, anchor in entries:
                    text = sections.get(anchor, "")
                    if text:
                        raw_chapters.append((title, text, True))
            else:
                text = soup.get_text(separator="\n", strip=True)
                if text:
                    raw_chapters.append((entries[0][0], text, True))
        else:
            text = soup.get_text(separator="\n", strip=True)
            if not text:
                continue
            heading = soup.find(["h1", "h2", "h3"])
            title = heading.get_text(strip=True) if heading else text.strip().split("\n")[0][:80]
            raw_chapters.append((title, text, False))
    chapters = []
    pending_text = ""
    for title, text, from_toc in raw_chapters:
        if not text:
            continue
        if not from_toc and len(text.strip()) < 50:
            pending_text += text.strip() + "\n"
            continue
        if pending_text:
            if not from_toc:
                title = pending_text.strip().split("\n")[0][:80]
            text = pending_text + text
            pending_text = ""
        chapters.append((title, text))
    if pending_text and chapters:
        t, txt = chapters[-1]
        chapters[-1] = (t, txt + "\n" + pending_text.strip())
    return chapters


def parse_with_spine(book):
    """Extract chapters in spine order (fallback when no TOC). Returns list of (title, text) tuples."""
    chapters = []
    pending_text = ""
    for spine_id, _ in book.spine:
        item = book.get_item_with_id(spine_id)
        if not item:
            continue
        soup = BeautifulSoup(item.get_body_content(), "html.parser")
        text = soup.get_text(separator="\n", strip=True)
        if not text:
            continue
        if len(text.strip()) < 50:
            pending_text += text.strip() + "\n"
            continue
        if pending_text:
            text = pending_text + text
            pending_text = ""
        heading = soup.find(["h1", "h2", "h3"])
        title = heading.get_text(strip=True) if heading else None
        if not title:
            title = text.strip().split("\n")[0][:80]
        chapters.append((title, text))
    if pending_text and chapters:
        title, text = chapters[-1]
        chapters[-1] = (title, text + "\n" + pending_text.strip())
    return chapters


def parse_epub(epub_path):
    """Extract chapters from epub using NCX TOC, falling back to spine order."""
    book = epub.read_epub(epub_path)
    toc_entries = flatten_toc(book.toc)
    if toc_entries:
        chapters = parse_with_toc(book, toc_entries)
        if chapters:
            return chapters
    return parse_with_spine(book)


def chunk_text(text, max_size=CHUNK_SIZE):
    """Split text into chunks at paragraph boundaries, respecting max_size."""
    paragraphs = text.split("\n")
    chunks = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 1 > max_size:
            if current:
                chunks.append(current)
            if len(para) > max_size:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                for sentence in sentences:
                    if len(current) + len(sentence) + 1 > max_size:
                        if current:
                            chunks.append(current)
                        current = sentence
                    else:
                        current = f"{current} {sentence}".strip()
            else:
                current = para
        else:
            current = f"{current}\n{para}".strip()
    if current:
        chunks.append(current)
    return chunks


def concat_mp3s(input_paths, output_path):
    """Concatenate MP3 files using ffmpeg."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for path in input_paths:
            escaped = str(Path(path).resolve()).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
        list_file = f.name
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", str(Path(output_path).resolve())],
            capture_output=True,
        )
        if result.returncode != 0:
            print(f"  ffmpeg error: {result.stderr.decode(errors='replace')[-500:]}")
            result.check_returncode()
    finally:
        Path(list_file).unlink()


async def tts_chunk(text, voice, rate, output_path):
    """Convert a single text chunk to MP3 via Edge TTS."""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_path)


def pad_width(total):
    """Return the number of digits needed to represent total."""
    return len(str(total))


async def convert_chapter(chapter_num, title, text, voice, rate, output_dir, chapter_pad):
    """Convert a full chapter to MP3, chunking if needed."""
    safe_title = re.sub(r"[^\w\s-]", "", title)[:50].strip()
    num = str(chapter_num).zfill(chapter_pad)
    filename = f"{num} {safe_title}.mp3" if safe_title else f"{num}.mp3"
    chapter_path = output_dir / filename
    if chapter_path.exists() and chapter_path.stat().st_size > 0:
        print(f"  Skipping (exists): {filename}")
        return chapter_path
    if chapter_path.exists():
        chapter_path.unlink()
    chunks = chunk_text(text)
    if not chunks:
        return None
    if len(chunks) == 1:
        await tts_chunk(chunks[0], voice, rate, str(chapter_path))
    else:
        chunk_pad = pad_width(len(chunks))
        tmp_dir = output_dir / "_tmp"
        tmp_dir.mkdir(exist_ok=True)
        chunk_paths = []
        for i, chunk in enumerate(chunks):
            tmp_path = tmp_dir / f"ch{str(chapter_num).zfill(chapter_pad)}_chunk{str(i).zfill(chunk_pad)}.mp3"
            if tmp_path.exists() and tmp_path.stat().st_size > 0:
                print(f"    Chunk {i} exists, skipping")
            else:
                if tmp_path.exists():
                    tmp_path.unlink()
                await tts_chunk(chunk, voice, rate, str(tmp_path))
            chunk_paths.append(tmp_path)
        concat_mp3s(chunk_paths, chapter_path)
        for p in chunk_paths:
            p.unlink()
        if not any(tmp_dir.iterdir()):
            tmp_dir.rmdir()
    return chapter_path


async def main():
    parser = argparse.ArgumentParser(description="Convert EPUB to audiobook using Edge TTS")
    parser.add_argument("epub_file", help="Path to .epub file")
    parser.add_argument("--voice", default="ru-RU-DmitryNeural", help="Edge TTS voice ID")
    parser.add_argument("--output", default="./output", help="Output directory")
    parser.add_argument("--no-merge", action="store_true", help="Skip creating combined MP3")
    parser.add_argument("--rate", default="+0%", help="Speech rate adjustment (e.g. +10%%, -5%%)")
    args = parser.parse_args()
    epub_path = Path(args.epub_file)
    if not epub_path.exists():
        print(f"Error: file not found: {epub_path}")
        sys.exit(1)
    book_name = epub_path.stem
    output_dir = Path(args.output) / book_name
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Parsing: {epub_path.name}")
    chapters = parse_epub(str(epub_path))
    if not chapters:
        print("Error: no chapters found in epub")
        sys.exit(1)
    print(f"Found {len(chapters)} chapters")
    print(f"Voice: {args.voice} | Rate: {args.rate}")
    print(f"Output: {output_dir}\n")
    chapter_pad = pad_width(len(chapters))
    chapter_paths = []
    for i, (title, text) in enumerate(chapters, 1):
        print(f"[{i}/{len(chapters)}] {title}")
        path = await convert_chapter(i, title, text, args.voice, args.rate, output_dir, chapter_pad)
        if path:
            chapter_paths.append(path)
    if not args.no_merge and len(chapter_paths) > 1:
        print(f"\nMerging {len(chapter_paths)} chapters...")
        merged_path = output_dir / f"{book_name}_complete.mp3"
        concat_mp3s(chapter_paths, merged_path)
        print(f"Complete audiobook: {merged_path}")
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
