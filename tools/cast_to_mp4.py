"""Export the demo asciinema cast to a 1080p H.264 video."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'demo'
sys.path.insert(0, str(OUT / '.video-tools'))
import pyte
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

rows = [json.loads(line) for line in (OUT / 'stopslop-demo.cast').read_text(encoding='utf-8').splitlines()]
header, events = rows[0], rows[1:]
screen = pyte.Screen(header['width'], header['height'])
stream = pyte.Stream(screen)
frames = OUT / '.video-frames'
frames.mkdir(exist_ok=True)
font = ImageFont.truetype('C:/Windows/Fonts/consola.ttf', 21)
bold = ImageFont.truetype('C:/Windows/Fonts/consolab.ttf', 21)
colors = {'default': '#d8e1ec', 'black': '#10151c', 'red': '#ff6b6b',
          'green': '#82d991', 'brown': '#eac471', 'blue': '#7db7ff',
          'magenta': '#c79af2', 'cyan': '#71d7e8', 'white': '#e8eef5',
          'brightblack': '#8995a5', 'brightred': '#ff8a8a',
          'brightgreen': '#a0efaa', 'brightbrown': '#ffde8b',
          'brightblue': '#a2cbff', 'brightmagenta': '#e0baff',
          'brightcyan': '#96efff', 'brightwhite': '#ffffff'}

def color(value):
    return colors.get(value, '#' + value if len(value) == 6 else '#d8e1ec')

def render(path):
    image = Image.new('RGB', (1920, 1080), '#10151c')
    draw = ImageDraw.Draw(image)
    for y in range(screen.lines):
        for x, char in screen.buffer[y].items():
            if not char.data.strip():
                continue
            fg, bg = char.fg, char.bg
            if char.reverse:
                fg, bg = bg, fg
            pos = (135 + x * 15, 33 + y * 22)
            if bg != 'default':
                draw.rectangle((pos[0], pos[1], pos[0] + 15, pos[1] + 22), fill=color(bg))
            draw.text(pos, char.data, font=bold if char.bold else font, fill=color(fg))
    image.save(path)

manifest = []
for index, event in enumerate(events):
    stream.feed(event[2])
    if index == len(events) - 1:
        continue
    duration = events[index + 1][0] - event[0]
    if duration <= 0:
        continue
    path = frames / f'{index:04d}.png'
    render(path)
    manifest += [f"file '{path.name}'", f'duration {duration:.6f}']
manifest.append(manifest[-2])
(frames / 'frames.txt').write_text('\n'.join(manifest) + '\n', encoding='utf-8')
target = OUT / 'stopslop-demo.mp4'
command = [imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-hide_banner', '-loglevel', 'warning',
           '-f', 'concat', '-safe', '0', '-i', str(frames / 'frames.txt'),
           '-vf', 'fps=30', '-t', '54', '-c:v', 'libx264', '-preset', 'fast',
           '-crf', '20', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(target)]
subprocess.run(command, check=True)
print(f'Saved {target} ({target.stat().st_size:,} bytes)', flush=True)
probe = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-i', str(target),
                        '-f', 'null', '-'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding='utf-8', check=True)
print(probe.stderr[-1800:])
