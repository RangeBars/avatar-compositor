import streamlit as st
import subprocess
import random
import os
import tempfile
import uuid
import re

st.set_page_config(page_title="Avatar Video Toolkit", layout="centered")
st.title("Avatar Video Toolkit")

tab1, tab2, tab3 = st.tabs(["Hook Builder", "Background Generator", "Variation Generator"])


# ============== HELPERS ==============

def save_upload(file, tmpdir, prefix):
    path = os.path.join(tmpdir, prefix + "_" + uuid.uuid4().hex[:6] + ".mp4")
    with open(path, "wb") as f:
        f.write(file.read())
    return path


def get_duration(path):
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path
    ]
    out = subprocess.check_output(cmd).strip()
    return float(out)


def has_audio(path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0", path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return "audio" in result.stdout


def add_silent_audio(path, tmpdir):
    out = os.path.join(tmpdir, "silent_" + uuid.uuid4().hex[:6] + ".mp4")
    cmd = [
        "ffmpeg", "-y", "-i", path,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v", "copy", "-c:a", "aac", "-shortest", out
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return out


def trim_video(path, max_seconds, tmpdir):
    dur = get_duration(path)
    if dur <= max_seconds:
        return path
    out = os.path.join(tmpdir, "trim_" + uuid.uuid4().hex[:6] + ".mp4")
    cmd = [
        "ffmpeg", "-y", "-i", path, "-t", str(max_seconds),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", out
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return out


def detect_silence_cuts(audio_path, min_silence=0.25, silence_db=-30):
    cmd = [
        "ffmpeg", "-i", audio_path,
        "-af", "silencedetect=noise=" + str(silence_db) + "dB:d=" + str(min_silence),
        "-f", "null", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    cuts = []
    for line in result.stderr.split("\n"):
        match = re.search(r"silence_end:\s*([\d.]+)", line)
        if match:
            cuts.append(float(match.group(1)))
    return cuts


def concat_videos(paths, w, h, output_path, tmpdir, crossfade=0):
    normalized = []
    for p in paths:
        if not has_audio(p):
            p = add_silent_audio(p, tmpdir)
        normalized.append(p)

    n = len(normalized)
    inputs = []
    for p in normalized:
        inputs.extend(["-i", p])

    if crossfade <= 0 or n == 1:
        parts = []
        concat_str = ""
        for i in range(n):
            parts.append("[" + str(i) + ":v]scale=" + str(w) + ":" + str(h) + ",setsar=1,fps=30[v" + str(i) + "]")
            concat_str += "[v" + str(i) + "][" + str(i) + ":a]"
        parts.append(concat_str + "concat=n=" + str(n) + ":v=1:a=1[outv][outa]")
        full_filter = ";".join(parts)
    else:
        durations = [get_duration(p) for p in normalized]
        parts = []
        for i in range(n):
            parts.append("[" + str(i) + ":v]scale=" + str(w) + ":" + str(h) + ",setsar=1,fps=30[v" + str(i) + "]")
        prev_v = "v0"
        prev_a = "0:a"
        offset = durations[0] - crossfade
        for i in range(1, n):
            out_v = "vx" + str(i) if i < n - 1 else "outv"
            out_a = "ax" + str(i) if i < n - 1 else "outa"
            parts.append(
                "[" + prev_v + "][v" + str(i) + "]xfade=transition=fade:duration=" +
                str(crossfade) + ":offset=" + ("%.2f" % offset) + "[" + out_v + "]"
            )
            parts.append(
                "[" + prev_a + "][" + str(i) + ":a]acrossfade=d=" + str(crossfade) + "[" + out_a + "]"
            )
            prev_v = out_v
            prev_a = out_a
            offset += durations[i] - crossfade
        full_filter = ";".join(parts)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", full_filter,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1500:])


def composite_avatar(bg_path, avatar_path, w, h, output_path, min_pause=0.25, silence_db=-30, fb_min=2.0, fb_max=3.5):
    duration = get_duration(avatar_path)
    pauses = detect_silence_cuts(avatar_path, min_pause, silence_db)

    cuts = [0.0]
    last = 0.0
    for p in pauses:
        if p - last >= 1.5:
            cuts.append(p)
            last = p

    final = [0.0]
    for i in range(1, len(cuts)):
        gap = cuts[i] - final[-1]
        if gap > fb_max + 1.0:
            t = final[-1] + random.uniform(fb_min, fb_max)
            while t < cuts[i]:
                final.append(t)
                t += random.uniform(fb_min, fb_max)
        final.append(cuts[i])

    if final[-1] < duration - 1.0:
        t = final[-1] + random.uniform(fb_min, fb_max)
        while t < duration:
            final.append(t)
            t += random.uniform(fb_min, fb_max)
    final.append(duration)

    zones = [
        (0.03, 0.05, 0.40),
        (0.57, 0.05, 0.40),
        (0.03, 0.55, 0.45),
        (0.55, 0.55, 0.45),
        (0.30, 0.30, 0.40),
        (0.03, 0.30, 0.45),
        (0.55, 0.30, 0.45),
        (0.20, 0.50, 0.55),
    ]

    segments = []
    last_zone = None
    for i in range(len(final) - 1):
        start = final[i]
        end = final[i + 1]
        if end - start < 0.5:
            continue
        choices = [z for z in zones if z != last_zone]
        zone = random.choice(choices)
        last_zone = zone
        x = int(w * zone[0])
        y = int(h * zone[1])
        segments.append((start, end, x, y, zone[2]))

    if len(segments) == 0:
        zone = random.choice(zones)
        segments = [(0, duration, int(w * zone[0]), int(h * zone[1]), zone[2])]

    parts = [
        "[0:v]scale=" + str(w) + ":" + str(h) + ",setsar=1[bg]",
        "[1:v]colorkey=0x00FF00:0.30:0.15[keyed]"
    ]
    chain = "[bg]"
    for i, (start, end, x, y, scale) in enumerate(segments):
        sw = int(w * scale)
        parts.append("[keyed]scale=" + str(sw) + ":-1[s" + str(i) + "]")
        next_label = "[v" + str(i) + "]" if i < len(segments) - 1 else "[outv]"
        chain += "[s" + str(i) + "]overlay=" + str(x) + ":" + str(y) + ":enable='between(t," + ("%.2f" % start) + "," + ("%.2f" % end) + ")'" + next_label
        if i < len(segments) - 1:
            parts.append(chain)
            chain = "[v" + str(i) + "]"
    parts.append(chain)
    full_filter = ";".join(parts)

    cmd = [
        "ffmpeg", "-y", "-i", bg_path, "-i", avatar_path,
        "-filter_complex", full_filter,
        "-map", "[outv]", "-map", "1:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-shortest", output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1500:])

    return len(segments)


def parse_size(s):
    nums = s.split(" ")[0].split("x")
    return int(nums[0]), int(nums[1])


# ============== TAB 1: HOOK BUILDER ==============

with tab1:
    st.header("Hook Builder")
    st.write("Combine Video A + Video B into finished hooks. Bulk mode supported.")

    a_files = st.file_uploader("Video A files (openers)", type=["mp4", "mov"], accept_multiple_files=True, key="a")
    b_files = st.file_uploader("Video B files (end scenes)", type=["mp4", "mov"], accept_multiple_files=True, key="b")

    mode = st.radio("Pairing mode", ["Every combination (A x B)", "Random pairings"], key="hbmode")
    n_random = 10
    if mode == "Random pairings":
        n_random = st.slider("How many random hooks?", 1, 50, 10, key="hbnum")

    size1 = st.selectbox("Output size", ["1080x1920 (vertical)", "1920x1080 (horizontal)", "1080x1080 (square)"], key="hbsize")
    xfade1 = st.checkbox("Crossfade between A and B (0.3s)", value=False, key="hbxf")

    if st.button("Build Hooks", type="primary", key="hbbtn"):
        if not a_files or not b_files:
            st.error("Upload at least one A and one B.")
        else:
            tmpdir = tempfile.gettempdir()
            w, h = parse_size(size1)

            with st.spinner("Saving uploads..."):
                a_paths = [(f.name, save_upload(f, tmpdir, "a")) for f in a_files]
                b_paths = [(f.name, save_upload(f, tmpdir, "b")) for f in b_files]

            if mode == "Every combination (A x B)":
                pairs = [(a, b) for a in a_paths for b in b_paths]
            else:
                pairs = [(random.choice(a_paths), random.choice(b_paths)) for _ in range(n_random)]

            st.info("Building " + str(len(pairs)) + " hooks...")
            prog = st.progress(0)
            status = st.empty()
            results = []

            for i, ((an, ap), (bn, bp)) in enumerate(pairs):
                status.text("Building " + str(i + 1) + " of " + str(len(pairs)))
                try:
                    out_path = os.path.join(tmpdir, "hook_" + str(i + 1) + "_" + uuid.uuid4().hex[:6] + ".mp4")
                    cf = 0.3 if xfade1 else 0
                    concat_videos([ap, bp], w, h, out_path, tmpdir, crossfade=cf)
                    results.append((i + 1, out_path, an, bn))
                except Exception as e:
                    st.error("Hook " + str(i + 1) + " failed: " + str(e)[:300])
                prog.progress((i + 1) / len(pairs))

            status.text("Done. " + str(len(results)) + " hooks built.")

            for idx, path, an, bn in results:
                with st.expander("Hook " + str(idx) + ": " + an + " + " + bn, expanded=(idx == 1)):
                    with open(path, "rb") as f:
                        data = f.read()
                    st.video(data)
                    st.download_button("Download Hook " + str(idx), data, file_name="hook_" + str(idx) + ".mp4", mime="video/mp4", key="hbdl_" + str(idx))


# ============== TAB 2: BACKGROUND GENERATOR ==============

with tab2:
    st.header("Background Generator")
    st.write("Stitch random clips from a library into unique backgrounds.")

    bg_lib = st.file_uploader("Background clip library", type=["mp4", "mov"], accept_multiple_files=True, key="bglib")

    target = st.slider("Target length (seconds)", 15, 180, 60, 5, key="bgtarget")
    cmin = st.slider("Shortest segment (seconds)", 1.0, 10.0, 3.0, 0.5, key="bgcmin")
    cmax = st.slider("Longest segment (seconds)", 2.0, 15.0, 6.0, 0.5, key="bgcmax")
    bgnum = st.slider("How many backgrounds?", 1, 10, 1, key="bgnum")
    xfade2 = st.checkbox("Crossfade between clips (0.3s)", value=True, key="bgxf")
    size2 = st.selectbox("Output size", ["1080x1920 (vertical)", "1920x1080 (horizontal)", "1080x1080 (square)"], key="bgsize")
    repeat = st.checkbox("Allow repeats in one background", value=False, key="bgrep")

    if st.button("Generate Backgrounds", type="primary", key="bgbtn"):
        if not bg_lib:
            st.error("Upload at least one clip.")
        else:
            tmpdir = tempfile.gettempdir()
            w, h = parse_size(size2)

            with st.spinner("Saving uploads..."):
                lib = [save_upload(f, tmpdir, "lib") for f in bg_lib]

            prog = st.progress(0)
            status = st.empty()
            results = []

            for v in range(bgnum):
                status.text("Generating background " + str(v + 1) + " of " + str(bgnum))
                try:
                    selected = []
                    used = set()
                    accum = 0.0

                    while accum < target:
                        if not repeat:
                            avail = [p for p in lib if p not in used]
                            if not avail:
                                used = set()
                                avail = lib
                        else:
                            avail = lib

                        clip = random.choice(avail)
                        used.add(clip)
                        clip_dur = get_duration(clip)
                        seg_len = random.uniform(cmin, min(cmax, clip_dur))
                        trimmed = trim_video(clip, seg_len, tmpdir)
                        selected.append(trimmed)
                        accum += get_duration(trimmed)

                    out_path = os.path.join(tmpdir, "bg_" + str(v + 1) + "_" + uuid.uuid4().hex[:6] + ".mp4")
                    cf = 0.3 if xfade2 else 0
                    concat_videos(selected, w, h, out_path, tmpdir, crossfade=cf)
                    final_path = trim_video(out_path, target, tmpdir)
                    final_dur = get_duration(final_path)

                    results.append((v + 1, final_path, final_dur, len(selected)))
                except Exception as e:
                    st.error("Background " + str(v + 1) + " failed: " + str(e)[:300])
                prog.progress((v + 1) / bgnum)

            status.text("Done. " + str(len(results)) + " backgrounds generated.")

            for idx, path, dur, nc in results:
                with st.expander("Background " + str(idx) + " (" + ("%.1f" % dur) + "s, " + str(nc) + " clips)", expanded=(idx == 1)):
                    with open(path, "rb") as f:
                        data = f.read()
                    st.video(data)
                    st.download_button("Download Background " + str(idx), data, file_name="background_" + str(idx) + ".mp4", mime="video/mp4", key="bgdl_" + str(idx))


# ============== TAB 3: VARIATION GENERATOR ==============

with tab3:
    st.header("Variation Generator")
    st.write("Cuts land on natural speech pauses (sentences, punch lines).")

    hooks = st.file_uploader("Finished hooks", type=["mp4", "mov"], accept_multiple_files=True, key="vghk")
    avs = st.file_uploader("HeyGen avatar videos (green screen)", type=["mp4", "mov"], accept_multiple_files=True, key="vgav")
    bgs = st.file_uploader("Backgrounds", type=["mp4", "mov"], accept_multiple_files=True, key="vgbg")

    nvar = st.slider("How many variations?", 1, 10, 3, key="vgnum")

    with st.expander("Advanced cut settings"):
        mp_v = st.slider("Min pause for cut (seconds)", 0.10, 1.0, 0.25, 0.05, key="vgmp")
        db_v = st.slider("Silence threshold (dB)", -50, -15, -30, 1, key="vgdb")
        fmin_v = st.slider("Fallback min cut", 1.0, 5.0, 2.0, 0.5, key="vgfmin")
        fmax_v = st.slider("Fallback max cut", 2.0, 8.0, 3.5, 0.5, key="vgfmax")

    size3 = st.selectbox("Output size", ["1080x1920 (vertical)", "1920x1080 (horizontal)", "1080x1080 (square)"], key="vgsize")

    if st.button("Generate Variations", type="primary", key="vgbtn"):
        if not hooks or not avs or not bgs:
            st.error("Upload at least one hook, one avatar, and one background.")
        else:
            tmpdir = tempfile.gettempdir()
            w, h = parse_size(size3)

            with st.spinner("Saving uploads..."):
                hk = [(f.name, save_upload(f, tmpdir, "hk")) for f in hooks]
                av = [(f.name, save_upload(f, tmpdir, "av")) for f in avs]
                bg = [(f.name, save_upload(f, tmpdir, "bg")) for f in bgs]

            prog = st.progress(0)
            status = st.empty()
            results = []

            for i in range(nvar):
                status.text("Generating " + str(i + 1) + " of " + str(nvar))
                try:
                    hn, hp = random.choice(hk)
                    an, ap = random.choice(av)
                    bn, bp = random.choice(bg)

                    main_path = os.path.join(tmpdir, "main_" + uuid.uuid4().hex[:6] + ".mp4")
                    final_path = os.path.join(tmpdir, "final_" + str(i + 1) + "_" + uuid.uuid4().hex[:6] + ".mp4")

                    nc = composite_avatar(bp, ap, w, h, main_path, mp_v, db_v, fmin_v, fmax_v)
                    concat_videos([hp, main_path], w, h, final_path, tmpdir)

                    results.append((i + 1, final_path, hn, an, bn, nc))
                except Exception as e:
                    st.error("Variation " + str(i + 1) + " failed: " + str(e)[:300])
                prog.progress((i + 1) / nvar)

            status.text("Done. " + str(len(results)) + " variations generated.")

            for idx, path, hn, an, bn, nc in results:
                with st.expander("Variation " + str(idx) + " (" + str(nc) + " cuts)", expanded=(idx == 1)):
                    st.caption("Hook: " + hn + " | Avatar: " + an + " | Background: " + bn)
                    with open(path, "rb") as f:
                        data = f.read()
                    st.video(data)
                    st.download_button("Download Variation " + str(idx), data, file_name="variation_" + str(idx) + ".mp4", mime="video/mp4", key="vgdl_" + str(idx))
