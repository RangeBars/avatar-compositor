import streamlit as st
import subprocess
import random
import os
import tempfile
import uuid
import re
import itertools

st.set_page_config(page_title="Avatar Video Generator", layout="centered")
st.title("Avatar Video Generator")
st.write("Upload your assets, click Activate, get unlimited unique variations.")


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
    scale_str = "scale=" + str(w) + ":" + str(h) + ":force_original_aspect_ratio=decrease,pad=" + str(w) + ":" + str(h) + ":(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30"
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


def composite_avatar(bg_path, avatar_path, w, h, output_path,
                     key_color, key_tolerance, key_softness,
                     bg_audio_volume,
                     min_pause=0.25, silence_db=-30, fb_min=2.0, fb_max=3.5):
    """
    Composite avatar over background with cuts on speech pauses.
    
    key_color: hex color to remove ('0x00FF00' for green, '0xFFFFFF' for white)
    key_tolerance: how aggressive the keying is (0.0 to 0.5)
    key_softness: how soft the edge is (0.0 to 0.5)
    bg_audio_volume: 0.0 to 1.0 — volume multiplier for background audio
    """
    duration = get_duration(avatar_path)
    pauses = detect_silence_cuts(avatar_path, min_pause, silence_db)

    # Build cut points
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

    # Position zones — (x_pct, y_pct, scale_of_width)
    # The avatar's height is auto-scaled proportionally, and we ensure y+height stays on screen
    zones = [
        (0.03, 0.05, 0.40),
        (0.57, 0.05, 0.40),
        (0.03, 0.55, 0.40),
        (0.57, 0.55, 0.40),
        (0.30, 0.30, 0.38),
        (0.03, 0.30, 0.42),
        (0.55, 0.30, 0.42),
        (0.20, 0.50, 0.50),
    ]

    # Build segments — one per cut interval
    segments = []
    last_zone = None
    for i in range(len(final) - 1):
        start = final[i]
        end = final[i + 1]
        if end - start < 0.5:
            continue
        choices = []
        for z in zones:
            if z != last_zone:
                choices.append(z)
        zone = random.choice(choices)
        last_zone = zone
        x = int(w * zone[0])
        y = int(h * zone[1])
        scale = zone[2]
        segments.append((start, end, x, y, scale))

    if len(segments) == 0:
        zone = random.choice(zones)
        segments = [(0, duration, int(w * zone[0]), int(h * zone[1]), zone[2])]

    n_segs = len(segments)
    
    # ============== VIDEO FILTER CHAIN ==============
    # Step A: scale background to canvas
    # Step B: chroma-key the avatar (remove key_color)
    # Step C: SPLIT the keyed avatar into N copies (one per segment) — this fixes the disappearing issue
    # Step D: scale each copy to its segment size
    # Step E: chain N overlays, each with its own enable window
    
    parts = []
    parts.append(
        "[0:v]scale=" + str(w) + ":" + str(h) +
        ":force_original_aspect_ratio=increase,crop=" + str(w) + ":" + str(h) +
        ",setsar=1,fps=30[bg]"
    )
    parts.append(
        "[1:v]colorkey=" + key_color + ":" + ("%.2f" % key_tolerance) + ":" + ("%.2f" % key_softness) +
        ",format=yuva420p[keyed]"
    )
    
    # Split keyed into N copies
    split_outputs = ""
    for i in range(n_segs):
        split_outputs += "[k" + str(i) + "]"
    parts.append("[keyed]split=" + str(n_segs) + split_outputs)
    
    # Scale each copy
    for i in range(n_segs):
        seg = segments[i]
        scale = seg[4]
        sw = int(w * scale)
        idx = str(i)
        parts.append("[k" + idx + "]scale=" + str(sw) + ":-2[s" + idx + "]")
    
    # Chain overlays
    chain_input = "[bg]"
    for i in range(n_segs):
        seg = segments[i]
        start = seg[0]
        end = seg[1]
        x = seg[2]
        y = seg[3]
        idx = str(i)
        if i < n_segs - 1:
            out_label = "[v" + idx + "]"
        else:
            out_label = "[outv]"
        start_str = ("%.2f" % start)
        end_str = ("%.2f" % end)
        enable_str = "enable='between(t," + start_str + "," + end_str + ")'"
        parts.append(
            chain_input + "[s" + idx + "]overlay=" + str(x) + ":" + str(y) +
            ":" + enable_str + out_label
        )
        chain_input = "[v" + idx + "]"
    
    # ============== AUDIO FILTER ==============
    # Mix background audio (lowered) with avatar audio (full volume)
    bg_audio_filter = ""
    if bg_audio_volume > 0:
        bg_audio_filter = (
            "[0:a]volume=" + ("%.2f" % bg_audio_volume) + "[bga];" +
            "[1:a]volume=1.0[ava];" +
            "[bga][ava]amix=inputs=2:duration=shortest:dropout_transition=0[outa]"
        )
        parts.append(bg_audio_filter)
        audio_map = "[outa]"
    else:
        # Just use avatar audio
        audio_map = "1:a?"
    
    full_filter = ";".join(parts)
    
    # Make sure background has audio (add silent if not)
    bg_for_input = bg_path
    if bg_audio_volume > 0 and not has_audio(bg_path):
        bg_for_input = add_silent_audio(bg_path, tmpdir=os.path.dirname(output_path))

    cmd = [
        "ffmpeg", "-y",
        "-i", bg_for_input,
        "-i", avatar_path,
        "-filter_complex", full_filter,
        "-map", "[outv]",
        "-map", audio_map,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac",
        "-shortest", output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])

    return n_segs


def parse_size(s):
    nums = s.split(" ")[0].split("x")
    return int(nums[0]), int(nums[1])


# ============== UI ==============

st.subheader("1. Hook section")
hook_files = st.file_uploader(
    "Hook intro variations (upload many)",
    type=["mp4", "mov"], accept_multiple_files=True, key="hooks"
)
transition_file = st.file_uploader(
    "Binding transition (one video — connects hook to background)",
    type=["mp4", "mov"], key="transition"
)

st.subheader("2. Main section")
bg_files = st.file_uploader(
    "Background videos (upload many)",
    type=["mp4", "mov"], accept_multiple_files=True, key="backgrounds"
)
heygen_files = st.file_uploader(
    "HeyGen avatar videos (upload one or more)",
    type=["mp4", "mov"], accept_multiple_files=True, key="heygen"
)

st.subheader("3. Settings")

col1, col2 = st.columns(2)
with col1:
    output_size = st.selectbox(
        "Output size",
        ["1080x1920 (vertical)", "1920x1080 (horizontal)", "1080x1080 (square)"],
        key="size"
    )
with col2:
    generation_mode = st.radio(
        "Generation mode",
        ["All possible combinations", "Set number of random variations"],
        key="mode"
    )

if generation_mode == "Set number of random variations":
    num_variations = st.slider("How many variations?", 1, 50, 10, key="numvar")
else:
    max_combos = st.slider("Max combinations to generate (safety cap)", 1, 100, 30, key="maxcombo")

st.markdown("**Avatar background removal**")
key_mode = st.radio(
    "What background did you use in HeyGen?",
    ["Green screen (#00FF00)", "White / no background", "Custom color"],
    key="keymode"
)

if key_mode == "Custom color":
    custom_hex = st.text_input("Hex color to remove (e.g., 0xFF00FF for magenta)", value="0x00FF00", key="customhex")

key_tolerance = st.slider(
    "Keying tolerance (higher = removes more)", 0.10, 0.50, 0.30, 0.05, key="ktol",
    help="If you see edges of background color around the avatar, increase this. If parts of the avatar disappear, decrease."
)
key_softness = st.slider(
    "Edge softness", 0.0, 0.50, 0.15, 0.05, key="ksoft",
    help="Higher = softer/smoother edges around the avatar."
)

bg_audio_volume = st.slider(
    "Background video audio volume", 0.0, 1.0, 0.30, 0.05, key="bgvol",
    help="0 = mute background audio (only avatar voice). 1.0 = full background volume. Recommended: 0.20 to 0.40."
)

with st.expander("Advanced cut settings (avatar movement)"):
    min_pause = st.slider("Minimum pause for cut (seconds)", 0.10, 1.0, 0.25, 0.05, key="minpause")
    silence_db = st.slider("Silence threshold (dB)", -50, -15, -30, 1, key="db")
    fb_min = st.slider("Fallback minimum cut (seconds)", 1.0, 5.0, 2.0, 0.5, key="fmin")
    fb_max = st.slider("Fallback maximum cut (seconds)", 2.0, 8.0, 3.5, 0.5, key="fmax")

# Show the math
if hook_files and bg_files and heygen_files:
    total_combos = len(hook_files) * len(bg_files) * len(heygen_files)
    st.info(
        "You have " + str(len(hook_files)) + " hooks, " +
        str(len(bg_files)) + " backgrounds, and " +
        str(len(heygen_files)) + " HeyGen videos. " +
        "Total possible combinations: " + str(total_combos)
    )

# ============== ACTIVATE ==============

if st.button("ACTIVATE — Generate Videos", type="primary", key="activate"):
    if not hook_files:
        st.error("Please upload at least one hook intro.")
    elif not transition_file:
        st.error("Please upload the binding transition video.")
    elif not bg_files:
        st.error("Please upload at least one background video.")
    elif not heygen_files:
        st.error("Please upload at least one HeyGen avatar video.")
    else:
        tmpdir = tempfile.gettempdir()
        w, h = parse_size(output_size)

        # Determine key color
        if key_mode == "Green screen (#00FF00)":
            key_color = "0x00FF00"
        elif key_mode == "White / no background":
            key_color = "0xFFFFFF"
        else:
            key_color = custom_hex.strip()
            if not key_color.startswith("0x"):
                key_color = "0x" + key_color.lstrip("#").lstrip("0x")

        # Save uploads
        with st.spinner("Saving uploads..."):
            hook_list = []
            for f in hook_files:
                hook_list.append((f.name, save_upload(f, tmpdir, "hook")))
            transition_path = save_upload(transition_file, tmpdir, "trans")
            bg_list = []
            for f in bg_files:
                bg_list.append((f.name, save_upload(f, tmpdir, "bg")))
            heygen_list = []
            for f in heygen_files:
                heygen_list.append((f.name, save_upload(f, tmpdir, "hg")))

        # Build the list of combinations to generate
        all_combos = list(itertools.product(hook_list, bg_list, heygen_list))

        if generation_mode == "All possible combinations":
            if len(all_combos) > max_combos:
                random.shuffle(all_combos)
                combos_to_make = all_combos[:max_combos]
            else:
                combos_to_make = all_combos
        else:
            combos_to_make = []
            for i in range(num_variations):
                combos_to_make.append((
                    random.choice(hook_list),
                    random.choice(bg_list),
                    random.choice(heygen_list)
                ))

        st.info("Generating " + str(len(combos_to_make)) + " final videos. This may take a few minutes.")
        prog = st.progress(0)
        status = st.empty()
        results = []

        for i in range(len(combos_to_make)):
            combo = combos_to_make[i]
            hook_pair = combo[0]
            bg_pair = combo[1]
            hg_pair = combo[2]
            hook_name = hook_pair[0]
            hook_path = hook_pair[1]
            bg_name = bg_pair[0]
            bg_path = bg_pair[1]
            hg_name = hg_pair[0]
            hg_path = hg_pair[1]

            status.text("Generating " + str(i + 1) + " of " + str(len(combos_to_make)))

            try:
                # Step 1: composite avatar onto background
                suffix1 = uuid.uuid4().hex[:6]
                main_path = os.path.join(tmpdir, "main_" + suffix1 + ".mp4")
                num_cuts = composite_avatar(
                    bg_path, hg_path, w, h, main_path,
                    key_color, key_tolerance, key_softness, bg_audio_volume,
                    min_pause, silence_db, fb_min, fb_max
                )

                # Step 2: concat hook + transition + composited main
                suffix2 = uuid.uuid4().hex[:6]
                final_path = os.path.join(tmpdir, "final_" + str(i + 1) + "_" + suffix2 + ".mp4")
                concat_videos(
                    [hook_path, transition_path, main_path],
                    w, h, final_path, tmpdir
                )

                results.append((i + 1, final_path, hook_name, bg_name, hg_name, num_cuts))
            except Exception as e:
                err_msg = str(e)[:400]
                st.error("Variation " + str(i + 1) + " failed: " + err_msg)

            prog.progress((i + 1) / len(combos_to_make))

        status.text("Done. " + str(len(results)) + " videos generated.")

        # Show results
        for r in results:
            idx = r[0]
            path = r[1]
            hn = r[2]
            bn = r[3]
            an = r[4]
            nc = r[5]
            label = "Variation " + str(idx) + " (" + str(nc) + " avatar cuts)"
            with st.expander(label, expanded=(idx == 1)):
                st.caption("Hook: " + hn + " | Background: " + bn + " | HeyGen: " + an)
                with open(path, "rb") as f:
                    data = f.read()
                st.video(data)
                st.download_button(
                    "Download Variation " + str(idx),
                    data,
                    file_name="variation_" + str(idx) + ".mp4",
                    mime="video/mp4",
                    key="dl_" + str(idx)
                )
