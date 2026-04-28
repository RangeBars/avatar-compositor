import streamlit as st
import subprocess
import random
import os
import tempfile
import uuid
import re
import itertools

st.set_page_config(page_title="Avatar Video Toolkit", layout="centered")
st.title("Avatar Video Toolkit")

tab1, tab2, tab3 = st.tabs(["Hook Builder", "Background Generator", "Variation Generator"])


# ============== HELPERS ==============

def save_upload(file, tmpdir, prefix):
    suffix = uuid.uuid4().hex[:6]
    path = os.path.join(tmpdir, prefix + "_" + suffix + ".mp4")
    with open(path, "wb") as f:
        f.write(file.read())
    return path


def get_duration(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", path]
    return float(subprocess.check_output(cmd).strip())


def has_audio(path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a",
           "-show_entries", "stream=codec_type", "-of", "csv=p=0", path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return "audio" in result.stdout


def add_silent_audio(path, tmpdir):
    suffix = uuid.uuid4().hex[:6]
    out = os.path.join(tmpdir, "silent_" + suffix + ".mp4")
    cmd = ["ffmpeg", "-y", "-i", path,
           "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
           "-c:v", "copy", "-c:a", "aac", "-shortest", out]
    subprocess.run(cmd, capture_output=True, check=True)
    return out


def detect_silence_cuts(audio_path, min_silence=0.25, silence_db=-30):
    af_arg = "silencedetect=noise=" + str(silence_db) + "dB:d=" + str(min_silence)
    cmd = ["ffmpeg", "-i", audio_path, "-af", af_arg, "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    cuts = []
    for line in result.stderr.split("\n"):
        match = re.search(r"silence_end:\s*([\d.]+)", line)
        if match:
            cuts.append(float(match.group(1)))
    return cuts


def concat_videos(paths, w, h, output_path, tmpdir):
    normalized = []
    for p in paths:
        if not has_audio(p):
            p = add_silent_audio(p, tmpdir)
        normalized.append(p)

    n = len(normalized)
    inputs = []
    for p in normalized:
        inputs.extend(["-i", p])

    parts = []
    concat_str = ""
    scale_str = "scale=" + str(w) + ":" + str(h) + ",setsar=1,fps=30"
    for i in range(n):
        idx = str(i)
        parts.append("[" + idx + ":v]" + scale_str + "[v" + idx + "]")
        concat_str += "[v" + idx + "][" + idx + ":a]"
    parts.append(concat_str + "concat=n=" + str(n) + ":v=1:a=1[outv][outa]")
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

    parts = []
    parts.append("[0:v]scale=" + str(w) + ":" + str(h) + ",setsar=1[bg]")
    parts.append("[1:v]colorkey=0x00FF00:0.30:0.15[keyed]")

    chain = "[bg]"
    for i, seg in enumerate(segments):
        start, end, x, y, scale = seg
        idx = str(i)
        sw = int(w * scale)
        parts.append("[keyed]scale=" + str(sw) + ":-1[s" + idx + "]")
        if i < len(segments) - 1:
            next_label = "[v" + idx + "]"
        else:
            next_label = "[outv]"
        start_str = ("%.2f" % start)
        end_str = ("%.2f" % end)
        enable_str = "enable='between(t," + start_str + "," + end_str + ")'"
        chain += "[s" + idx + "]overlay=" + str(x) + ":" + str(y) + ":" + enable_str + next_label
        if i < len(segments) - 1:
            parts.append(chain)
            chain = "[v" + idx + "]"
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


def factorial(n):
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result


# ============== TAB 1: HOOK BUILDER ==============

with tab1:
    st.header("Hook Builder")
    st.write("Upload many Video A's and one Video B. Each output: Video B then Video A.")

    a_files = st.file_uploader("Video A files (upload many variations)", type=["mp4", "mov"], accept_multiple_files=True, key="a")
    b_file = st.file_uploader("Video B (upload one - plays first)", type=["mp4", "mov"], key="b")

    size1 = st.selectbox("Output size", ["1080x1920 (vertical)", "1920x1080 (horizontal)", "1080x1080 (square)"], key="hbsize")

    if st.button("Build Hooks", type="primary", key="hbbtn"):
        if not a_files or not b_file:
            st.error("Upload at least one Video A and one Video B.")
        else:
            tmpdir = tempfile.gettempdir()
            w, h = parse_size(size1)

            with st.spinner("Saving uploads..."):
                a_paths = []
                for f in a_files:
                    a_paths.append((f.name, save_upload(f, tmpdir, "a")))
                b_path = save_upload(b_file, tmpdir, "b")

            st.info("Building " + str(len(a_paths)) + " hooks...")
            prog = st.progress(0)
            status = st.empty()
            results = []

            for i in range(len(a_paths)):
                an = a_paths[i][0]
                ap = a_paths[i][1]
                status.text("Building " + str(i + 1) + " of " + str(len(a_paths)) + ": B + " + an)
                try:
                    suffix = uuid.uuid4().hex[:6]
                    out_path = os.path.join(tmpdir, "hook_" + str(i + 1) + "_" + suffix + ".mp4")
                    concat_videos([b_path, ap], w, h, out_path, tmpdir)
                    results.append((i + 1, out_path, an))
                except Exception as e:
                    st.error("Hook " + str(i + 1) + " failed: " + str(e)[:300])
                prog.progress((i + 1) / len(a_paths))

            status.text("Done. " + str(len(results)) + " hooks built.")

            for r in results:
                idx = r[0]
                path = r[1]
                an = r[2]
                with st.expander("Hook " + str(idx) + ": B + " + an, expanded=(idx == 1)):
                    with open(path, "rb") as f:
                        data = f.read()
                    st.video(data)
                    st.download_button("Download Hook " + str(idx), data,
                                       file_name="hook_" + str(idx) + ".mp4",
                                       mime="video/mp4", key="hbdl_" + str(idx))


# ============== TAB 2: BACKGROUND GENERATOR ==============

with tab2:
    st.header("Background Generator")
    st.write("Upload background clips. Generates every possible order (permutation), capped by your max.")

    bg_lib = st.file_uploader("Background clips", type=["mp4", "mov"], accept_multiple_files=True, key="bglib")

    if bg_lib:
        n_clips = len(bg_lib)
        total_perms = factorial(n_clips)
        st.info("With " + str(n_clips) + " clips, there are " + str(total_perms) + " possible orderings.")

    max_output = st.slider("Max number of variations to generate", 1, 50, 10, key="bgmax")
    size2 = st.selectbox("Output size", ["1080x1920 (vertical)", "1920x1080 (horizontal)", "1080x1080 (square)"], key="bgsize")

    if st.button("Generate Backgrounds", type="primary", key="bgbtn"):
        if not bg_lib or len(bg_lib) < 2:
            st.error("Upload at least 2 clips.")
        else:
            tmpdir = tempfile.gettempdir()
            w, h = parse_size(size2)

            with st.spinner("Saving uploads..."):
                lib = []
                for f in bg_lib:
                    lib.append((f.name, save_upload(f, tmpdir, "lib")))

            all_perms = list(itertools.permutations(lib))
            if len(all_perms) > max_output:
                all_perms = random.sample(all_perms, max_output)

            st.info("Generating " + str(len(all_perms)) + " variations...")
            prog = st.progress(0)
            status = st.empty()
            results = []

            for v in range(len(all_perms)):
                perm = all_perms[v]
                names = []
                paths = []
                for p in perm:
                    names.append(p[0])
                    paths.append(p[1])
                status.text("Generating " + str(v + 1) + " of " + str(len(all_perms)) + ": " + " > ".join(names))
                try:
                    suffix = uuid.uuid4().hex[:6]
                    out_path = os.path.join(tmpdir, "bg_" + str(v + 1) + "_" + suffix + ".mp4")
                    concat_videos(paths, w, h, out_path, tmpdir)
                    results.append((v + 1, out_path, names))
                except Exception as e:
                    st.error("Background " + str(v + 1) + " failed: " + str(e)[:300])
                prog.progress((v + 1) / len(all_perms))

            status.text("Done. " + str(len(results)) + " backgrounds generated.")

            for r in results:
                idx = r[0]
                path = r[1]
                names = r[2]
                with st.expander("Background " + str(idx) + ": " + " > ".join(names), expanded=(idx == 1)):
                    with open(path, "rb") as f:
                        data = f.read()
                    st.video(data)
                    st.download_button("Download Background " + str(idx), data,
                                       file_name="background_" + str(idx) + ".mp4",
                                       mime="video/mp4", key="bgdl_" + str(idx))


# ============== TAB 3: VARIATION GENERATOR ==============

with tab3:
    st.header("Variation Generator")
    st.write("Combine hooks + HeyGen avatar (green screen) + backgrounds. Cuts land on speech pauses.")

    hooks = st.file_uploader("Finished hooks (from Tab 1)", type=["mp4", "mov"], accept_multiple_files=True, key="vghk")
    avs = st.file_uploader("HeyGen avatar videos (green screen)", type=["mp4", "mov"], accept_multiple_files=True, key="vgav")
    bgs = st.file_uploader("Backgrounds (from Tab 2)", type=["mp4", "mov"], accept_multiple_files=True, key="vgbg")

    nvar = st.slider("How many variations?", 1, 20, 5, key="vgnum")

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
                hk = []
                for f in hooks:
                    hk.append((f.name, save_upload(f, tmpdir, "hk")))
                av = []
                for f in avs:
                    av.append((f.name, save_upload(f, tmpdir, "av")))
                bg = []
                for f in bgs:
                    bg.append((f.name, save_upload(f, tmpdir, "bg")))

            prog = st.progress(0)
            status = st.empty()
            results = []

            for i in range(nvar):
                status.text("Generating " + str(i + 1) + " of " + str(nvar))
                try:
                    chosen_hook = random.choice(hk)
                    chosen_av = random.choice(av)
                    chosen_bg = random.choice(bg)
                    hn = chosen_hook[0]
                    hp = chosen_hook[1]
                    an = chosen_av[0]
                    ap = chosen_av[1]
                    bn = chosen_bg[0]
                    bp = chosen_bg[1]

                    suffix1 = uuid.uuid4().hex[:6]
                    suffix2 = uuid.uuid4().hex[:6]
                    main_path = os.path.join(tmpdir, "main_" + suffix1 + ".mp4")
                    final_path = os.path.join(tmpdir, "final_" + str(i + 1) + "_" + suffix2 + ".mp4")

                    nc = composite_avatar(bp, ap, w, h, main_path, mp_v, db_v, fmin_v, fmax_v)
                    concat_videos([hp, main_path], w, h, final_path, tmpdir)

                    results.append((i + 1, final_path, hn, an, bn, nc))
                except Exception as e:
                    st.error("Variation " + str(i + 1) + " failed: " + str(e)[:300])
                prog.progress((i + 1) / nvar)

            status.text("Done. " + str(len(results)) + " variations generated.")

            for r in results:
                idx = r[0]
                path = r[1]
                hn = r[2]
                an = r[3]
                bn = r[4]
                nc = r[5]
                with st.expander("Variation " + str(idx) + " (" + str(nc) + " cuts)", expanded=(idx == 1)):
                    st.caption("Hook: " + hn + " | Avatar: " + an + " | Background: " + bn)
                    with open(path, "rb") as f:
                        data = f.read()
                    st.video(data)
                    st.download_button("Download Variation " + str(idx), data,
                                       file_name="variation_" + str(idx) + ".mp4",
                                       mime="video/mp4", key="vgdl_" + str(idx))
