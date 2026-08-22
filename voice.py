from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentServer, AgentSession, Agent, inference, room_io, TurnHandlingOptions
from livekit.plugins import ai_coustics,groq,silero

load_dotenv()


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
        instructions = """
            You are AlterEgo, a ultra-fast, intelligent AI assistant and smart glasses persona.

            Core Behavioral Rules:
            1. Speak naturally, concisely, and directly—like a sharp, helpful human partner.
            2. Keep replies brief (1 to 3 sentences maximum) unless specifically asked for a detailed breakdown.
            3. Avoid markdown formatting, bullet points, asterisks, bold text, or code blocks in your output, as your responses will be read aloud by Text-to-Speech (TTS).
            4. Do not use robotic filler phrases like "How can I help you today?" or "As an AI...".
            5. Speak in clear, plain spoken English. Use natural conversational pauses where appropriate.
        """)

server = AgentServer()

@server.rtc_session(agent_name="AlterEgo")
async def AtlerEgo(ctx: agents.JobContext):
    assistant = AgentSession(
        stt=inference.STT(model="deepgram/nova-3"),
        llm=groq.LLM(model="openai/gpt-oss-20b"),
        tts=inference.TTS(
            model="inworld/inworld-tts-2",
            voice="Ashley",
        ),
        vad=silero.VAD.load(
            min_silence_duration=0.3,
            min_speech_duration=0.05,
        ),
        turn_handling=TurnHandlingOptions(
            min_endpointing_delay=0.2,
        ),
    )

    await assistant.start(
        room=ctx.room,
        agent=Assistant(),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(model=ai_coustics.EnhancerModel.QUAIL_VF_S),
            ),
        ),
    )

    await assistant.say("AlterEgo online. systems nominal.")

if __name__ == "__main__":
    agents.cli.run_app(server)