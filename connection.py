import asyncio
import os
import time
import wave
from pathlib import Path

import numpy as np
import websockets
from dotenv import load_dotenv
from livekit import api, rtc

load_dotenv()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
ROOM_NAME = "alterego-glasses"
AGENT_NAME = "AlterEgo"

# Fixed identity: a reconnect replaces the old participant instead of adding a second one.
CLIENT_ID = "esp32-glasses"

SAMPLE_RATE = 16000
CHANNELS = 1

AGENT_WAIT_S = 20  # how long to wait for the agent to join after dispatching

# The agent's audio track streams silent frames while it is NOT speaking. Forwarding them keeps
# the glasses in "playback" mode and the mic muted forever, so frames below this peak are dropped
# (a short hangover keeps natural pauses inside speech).
SILENCE_PEAK = int(os.getenv("AGENT_SILENCE_PEAK", "100"))
HANGOVER_S = 0.4

# Set SAVE_CAPTURE=1 in .env to save what the glasses' mic sent (captures/*.wav).
SAVE_CAPTURE = os.getenv("SAVE_CAPTURE", "0") == "1"
CAPTURE_DIR = Path("captures")

# Only one ESP32 session at a time. A new connection evicts the old one first.
_active = {"ws": None, "done": None}


def create_token(identity: str) -> str:
    # No agent dispatch here on purpose: after a room reset the token dispatch can silently
    # not fire. We dispatch the agent explicitly after joining (see dispatch_agent).
    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name("AlterEgo Hardware")
        .with_grants(api.VideoGrants(room_join=True, room=ROOM_NAME))
    )
    return token.to_jwt()


def make_api() -> api.LiveKitAPI:
    return api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)


async def safe_send(ws, payload):
    try:
        await ws.send(payload)
    except websockets.ConnectionClosed:
        pass


def is_agent(participant) -> bool:
    return participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT


def audible(pcm: bytes, hang: dict, now: float):
    """Return (forward_this_frame, peak). Silent frames are dropped outside the hangover."""
    pcm = pcm[: len(pcm) // 2 * 2]
    if not pcm:
        return False, 0
    peak = int(np.max(np.abs(np.frombuffer(pcm, dtype=np.int16).astype(np.int32))))
    if peak >= SILENCE_PEAK:
        hang["until"] = now + HANGOVER_S
        return True, peak
    return now < hang["until"], peak


def agent_present(room: rtc.Room) -> bool:
    return any(is_agent(p) for p in room.remote_participants.values())


async def reset_room(client_id: str):
    """Delete the room and WAIT until it is really gone. Joining a room that is still being
    torn down is what made the agent arrive 20 seconds late."""
    try:
        async with make_api() as lkapi:
            await lkapi.room.delete_room(api.DeleteRoomRequest(room=ROOM_NAME))
            print(f"[{client_id}] Old room deleted, waiting for it to disappear...")
            for _ in range(20):  # up to ~10s
                resp = await lkapi.room.list_rooms(api.ListRoomsRequest(names=[ROOM_NAME]))
                if not resp.rooms:
                    break
                await asyncio.sleep(0.5)
        await asyncio.sleep(0.5)
        print(f"[{client_id}] Room is clean.")
    except Exception as e:
        # Normal on the very first run: there is no room to delete yet.
        print(f"[{client_id}] Room reset skipped: {e!r}")


async def dispatch_agent(room: rtc.Room, client_id: str):
    """Start exactly one agent, right after joining, then confirm it shows up."""
    for attempt in range(3):
        try:
            async with make_api() as lkapi:
                existing = await lkapi.agent_dispatch.list_dispatch(room_name=ROOM_NAME)
                if existing:
                    print(f"[{client_id}] Agent dispatch already exists.")
                else:
                    await lkapi.agent_dispatch.create_dispatch(
                        api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=ROOM_NAME)
                    )
                    print(f"[{client_id}] Agent dispatched.")
            break
        except Exception as e:
            print(f"[{client_id}] Dispatch attempt {attempt + 1} failed: {e!r}")
            await asyncio.sleep(1)

    for _ in range(AGENT_WAIT_S * 2):
        if agent_present(room):
            return
        await asyncio.sleep(0.5)
    print(
        f"[{client_id}] No agent joined after {AGENT_WAIT_S}s. "
        f"Is voice.py running and registered as '{AGENT_NAME}'?"
    )


def save_capture(rec: bytearray, client_id: str):
    if not SAVE_CAPTURE or len(rec) < SAMPLE_RATE * 2:  # skip clips under 1s
        return
    try:
        CAPTURE_DIR.mkdir(exist_ok=True)
        name = CAPTURE_DIR / f"capture_{int(time.time())}.wav"
        with wave.open(str(name), "wb") as w:
            w.setnchannels(CHANNELS)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(bytes(rec))
        print(f"[{client_id}] Saved {name} ({len(rec) / 2 / SAMPLE_RATE:.1f}s)")
    except Exception as e:
        print(f"[{client_id}] Could not save capture: {e!r}")


async def run_session(ws):
    client_id = CLIENT_ID
    print(f"[{client_id}] ESP32 connected over WebSocket!")

    room = rtc.Room()
    mic_source = rtc.AudioSource(SAMPLE_RATE, CHANNELS)
    mic_track = rtc.LocalAudioTrack.create_audio_track("esp32-mic", mic_source)

    state = {"primary": None, "forward": None, "closing": False}
    helper_tasks = []

    @room.on("data_received")
    def on_data(data: rtc.DataPacket):
        if data.topic != "hud_text":
            return
        sender = data.participant
        if sender is not None and state["primary"] and sender.identity != state["primary"]:
            return  # ignore text from any extra agent
        msg = data.data.decode("utf-8", errors="replace")
        print(f"[HUD] {msg}")
        asyncio.create_task(safe_send(ws, f"TEXT:{msg}"))

    @room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication, participant):
        if track.kind != rtc.TrackKind.KIND_AUDIO or not is_agent(participant):
            return

        # Only ever forward ONE agent's voice. A second agent would double the greeting
        # and keep the glasses in playback mode, which silences the mic.
        if state["primary"] is None:
            state["primary"] = participant.identity
        elif state["primary"] != participant.identity:
            print(f"[{client_id}] Ignoring extra agent '{participant.identity}'.")
            return

        print(f"[{client_id}] Subscribed to agent voice track ({participant.identity}).")

        old = state["forward"]
        if old and not old.done():
            old.cancel()

        audio_stream = rtc.AudioStream(
            track, sample_rate=SAMPLE_RATE, num_channels=CHANNELS
        )

        async def forward_agent_audio():
            hang = {"until": 0.0}
            st = {"total": 0, "dropped": 0, "drop_peak": 0, "t": time.monotonic()}
            try:
                async for frame_event in audio_stream:
                    pcm = frame_event.frame.data.tobytes()
                    now = time.monotonic()
                    ok, peak = audible(pcm, hang, now)
                    st["total"] += 1
                    if ok:
                        try:
                            await ws.send(pcm)
                        except websockets.ConnectionClosed:
                            break
                    else:
                        st["dropped"] += 1
                        st["drop_peak"] = max(st["drop_peak"], peak)
                    if now - st["t"] >= 5:
                        print(
                            f"[{client_id}] agent audio: {st['total']} frames, "
                            f"{st['dropped']} silent dropped (max silent peak {st['drop_peak']})"
                        )
                        st.update(total=0, dropped=0, drop_peak=0, t=now)
            except asyncio.CancelledError:
                pass
            finally:
                await audio_stream.aclose()

        state["forward"] = asyncio.create_task(forward_agent_audio())

    @room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        # If the agent leaves, close the ESP32 socket. The ESP32 reconnects and gets a
        # clean room with a fresh agent, instead of talking to nobody.
        if state["primary"] and participant.identity == state["primary"] and not state["closing"]:
            print(f"[{client_id}] Agent left the room, resetting the session.")
            asyncio.create_task(ws.close())

    @room.on("disconnected")
    def on_disconnected(reason=None):
        if not state["closing"]:
            print(f"[{client_id}] LiveKit room disconnected: {reason}")
            asyncio.create_task(ws.close())

    rec = bytearray()
    try:
        await reset_room(client_id)
        await room.connect(LIVEKIT_URL, create_token(client_id))

        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await room.local_participant.publish_track(mic_track, options)
        print(f"[{client_id}] Joined LiveKit room '{ROOM_NAME}'.")

        helper_tasks.append(asyncio.create_task(dispatch_agent(room, client_id)))

        frame_count = 0
        async for message in ws:
            if not isinstance(message, bytes):
                continue

            if len(message) % 2:  # int16 samples need an even byte count
                message = message[:-1]
            if not message:
                continue

            samples = np.frombuffer(message, dtype=np.int16)
            frame_count += 1
            if frame_count % 20 == 0:
                peak = int(np.max(np.abs(samples.astype(np.int32))))
                print(f"[{client_id}] Mic peak: {peak}")

            if SAVE_CAPTURE:
                rec.extend(message)

            await mic_source.capture_frame(
                rtc.AudioFrame(
                    data=message,
                    sample_rate=SAMPLE_RATE,
                    num_channels=CHANNELS,
                    samples_per_channel=len(samples),
                )
            )

    except websockets.ConnectionClosed as e:
        print(f"[{client_id}] ESP32 disconnected: {e}")
    except Exception as e:
        print(f"[{client_id}] Session error: {e!r}")
    finally:
        state["closing"] = True
        save_capture(rec, client_id)
        for t in helper_tasks:
            t.cancel()
        fwd = state["forward"]
        if fwd and not fwd.done():
            fwd.cancel()
        try:
            await room.disconnect()
        except Exception:
            pass
        print(f"[{client_id}] Session cleaned up.")


async def handle_esp32(ws):
    # Evict a stale session first, so the room never has two glasses sessions.
    prev_ws, prev_done = _active["ws"], _active["done"]
    if prev_ws is not None:
        print(f"[{CLIENT_ID}] New connection, closing the old session first...")
        try:
            await prev_ws.close()
        except Exception:
            pass
        if prev_done is not None:
            try:
                await asyncio.wait_for(prev_done.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    done = asyncio.Event()
    _active["ws"], _active["done"] = ws, done
    try:
        await run_session(ws)
    finally:
        if _active["ws"] is ws:
            _active["ws"], _active["done"] = None, None
        done.set()


async def main():
    async with websockets.serve(
        handle_esp32, "0.0.0.0", 8765, max_size=None, ping_interval=None
    ):
        print("WebSocket Bridge listening on ws://0.0.0.0:8765")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass