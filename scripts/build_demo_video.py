"""Build the narrated demo video from docs/submission/video-script.json (Apache-2.0).

1. Narration per segment: a recorded <id>.wav from --voice-dir if present, otherwise offline
   Windows speech (scripts/tts_windows.ps1). Nothing is sent to any service.
2. Title cards and still images are rendered to 1440x900 PNG with the system Chrome.
3. The app part is recorded by scripts/capture_demo.py on an isolated API with no model key;
   every beat holds for its narration and timeline.json records when each beat started.
4. ffmpeg places every narration clip on the timeline, encodes H.264/AAC parts, concatenates
   them and writes matching SRT subtitles. The output directory must not exist yet.

Usage: python scripts/build_demo_video.py --output .local/demo/video-v1 [--voice-dir my-recordings]
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'docs/submission/video-script.json'
WIDTH, HEIGHT, FPS = 1440, 900, 25
PAD_S = 0.8          # silence after each card's narration
HOLD_PAD_MS = 600    # extra hold after each app beat's narration
AUDIO_OFFSET_MS = 300  # the video starts slightly before the spec's clock


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, encoding='utf-8', **kwargs)


def seconds(path: Path) -> float:
    with wave.open(str(path)) as audio:
        return audio.getnframes() / audio.getframerate()


def narrate(segments, folder: Path, voice_dir: Path | None, voice: str) -> str:
    jobs = []
    for segment in segments:
        target = folder / f"{segment['id']}.wav"
        recorded = voice_dir / f"{segment['id']}.wav" if voice_dir else None
        if recorded and recorded.exists():
            shutil.copyfile(recorded, target)
        else:
            jobs.append({'text': segment['narration'], 'path': str(target)})
    if not jobs:
        return 'recorded'
    (folder / 'tts-jobs.json').write_text(json.dumps(jobs, ensure_ascii=False), encoding='utf-8')
    result = run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'scripts/tts_windows.ps1'),
                  '-Jobs', str(folder / 'tts-jobs.json'), *(['-Voice', voice] if voice else [])])
    return result.stdout.strip().splitlines()[-1] if len(jobs) == len(segments) else 'mixed: ' + result.stdout.strip()


def card_html(segment) -> str:
    e = html.escape
    if segment['kind'] == 'image':
        source = (ROOT / segment['image']).resolve().as_uri()
        body = f'<img src="{source}" style="max-width:92%;max-height:86%">'
    else:
        body = (f'<div style="font-size:64px;font-weight:700;color:#1f5f54">{e(segment["title"])}</div>'
                f'<div style="font-size:30px;color:#2c4a43;margin-top:28px;max-width:1150px">{e(segment["subtitle"])}</div>')
    return ('<!doctype html><html><head><meta charset="utf-8"></head>'
            '<body style="margin:0;width:1440px;height:900px;display:flex;flex-direction:column;align-items:center;'
            'justify-content:center;text-align:center;background:#ffffff;'
            'font-family:\'Microsoft YaHei\',\'PingFang SC\',sans-serif">' + body + '</body></html>')


def encode_still(png: Path, audio: Path, duration: float, target: Path):
    run(['ffmpeg', '-y', '-loop', '1', '-framerate', str(FPS), '-t', f'{duration:.3f}', '-i', str(png), '-i', str(audio),
         '-filter_complex', f'[0:v]scale={WIDTH}:{HEIGHT},format=yuv420p[v];[1:a]apad,atrim=0:{duration:.3f}[a]',
         '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-r', str(FPS), '-c:a', 'aac', '-ar', '48000', '-ac', '2',
         '-t', f'{duration:.3f}', str(target)])


def encode_app(webm: Path, beats, folder: Path, target: Path) -> float:
    probe = run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(webm)])
    duration = float(probe.stdout.strip())
    inputs, filters = ['-i', str(webm)], []
    for index, (beat_id, start_ms) in enumerate(beats, 1):
        inputs += ['-i', str(folder / f'{beat_id}.wav')]
        delay = start_ms + AUDIO_OFFSET_MS
        filters.append(f'[{index}:a]adelay={delay}|{delay}[a{index}]')
    mix = ''.join(f'[a{i}]' for i in range(1, len(beats) + 1))
    filters.append(f'{mix}amix=inputs={len(beats)}:normalize=0,apad,atrim=0:{duration:.3f}[a]')
    filters.append(f'[0:v]scale={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p[v]')
    run(['ffmpeg', '-y', *inputs, '-filter_complex', ';'.join(filters), '-map', '[v]', '-map', '[a]',
         '-c:v', 'libx264', '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-t', f'{duration:.3f}', str(target)])
    return duration


def stamp(value: float) -> str:
    milliseconds = round(value * 1000)
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--voice-dir', type=Path, help='recorded <segment id>.wav files that replace synthetic speech')
    parser.add_argument('--voice', default='', help='Windows voice name; default is the first zh-CN voice')
    options = parser.parse_args(argv)
    if options.output.exists():
        parser.error('Refusing to overwrite an existing output directory')
    for tool in ('ffmpeg', 'ffprobe', 'node'):
        if not shutil.which(tool):
            parser.error(f'{tool} is required')
    output = options.output.resolve()
    audio, parts, pages = output / 'audio', output / 'parts', output / 'pages'
    for folder in (audio, parts, pages):
        folder.mkdir(parents=True)
    segments = json.loads(SCRIPT.read_text(encoding='utf-8'))['segments']
    voice = narrate(segments, audio, options.voice_dir, options.voice)
    lengths = {s['id']: seconds(audio / f"{s['id']}.wav") for s in segments}

    stills = [s for s in segments if s['kind'] in ('card', 'image')]
    jobs = []
    for segment in stills:
        page = pages / f"{segment['id']}.html"
        page.write_text(card_html(segment), encoding='utf-8')
        jobs.append({'html': str(page), 'png': str(pages / f"{segment['id']}.png"), 'width': WIDTH, 'height': HEIGHT})
    (pages / 'jobs.json').write_text(json.dumps(jobs), encoding='utf-8')
    run(['node', str(ROOT / 'scripts/render_pages.cjs'), str(pages / 'jobs.json')])

    app = [s for s in segments if s['kind'] == 'app']
    beats_file = output / 'beats.json'
    beats_file.write_text(json.dumps({'beats': {s['id']: {'caption': s['caption'],
                                                          'hold_ms': round(lengths[s['id']] * 1000) + HOLD_PAD_MS}
                                                for s in app}}, ensure_ascii=False), encoding='utf-8')
    run([sys.executable, str(ROOT / 'scripts/capture_demo.py'), '--output', str(output / 'recording'), '--video',
         '--script', str(beats_file)])
    timeline = json.loads((output / 'recording/shots/timeline.json').read_text(encoding='utf-8'))
    starts = {beat['id']: beat['start_ms'] for beat in timeline['beats']}
    if set(starts) != {s['id'] for s in app}:
        raise SystemExit('The recording did not reach every scripted beat')
    webm = next((output / 'recording/artifacts').rglob('*.webm'))

    order, cues, clock = [], [], 0.0
    app_done = False
    for segment in segments:
        if segment['kind'] == 'app':
            if app_done:
                continue
            app_done = True
            target = parts / 'app.mp4'
            duration = encode_app(webm, [(s['id'], starts[s['id']]) for s in app], audio, target)
            for s in app:
                begin = clock + (starts[s['id']] + AUDIO_OFFSET_MS) / 1000
                cues.append((begin, begin + lengths[s['id']], s['narration']))
        else:
            target = parts / f"{segment['id']}.mp4"
            duration = lengths[segment['id']] + PAD_S
            encode_still(pages / f"{segment['id']}.png", audio / f"{segment['id']}.wav", duration, target)
            cues.append((clock, clock + lengths[segment['id']], segment['narration']))
        order.append(target)
        clock += duration
    listing = output / 'parts.txt'
    listing.write_text(''.join(f"file '{part.as_posix()}'\n" for part in order), encoding='utf-8')
    final = output / 'ledgerlens-demo.mp4'
    run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing), '-c', 'copy', str(final)])
    (output / 'ledgerlens-demo.srt').write_text(
        ''.join(f'{i}\n{stamp(a)} --> {stamp(b)}\n{text}\n\n' for i, (a, b, text) in enumerate(cues, 1)),
        encoding='utf-8')
    report = {'schema_version': 'demo-video-v1', 'voice': voice, 'duration_seconds': round(clock, 1),
              'segments': len(segments), 'model_requests': 0,
              'video_sha256': hashlib.sha256(final.read_bytes()).hexdigest(),
              'script_sha256': hashlib.sha256(SCRIPT.read_bytes()).hexdigest()}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
