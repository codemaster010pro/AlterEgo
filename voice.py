from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentServer, AgentSession, Agent, inference, room_io, TurnHandlingOptions
from livekit.plugins import ai_coustics,openai,deepgram,silero
from openai import AsyncOpenAI
import os
import asyncio

load_dotenv()

class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
        instructions = """You are AlterEgo smart glasses AI. Answer in 1 short sentence. ASCII text only.""")

server = AgentServer()

@server.rtc_session(agent_name="AlterEgo")
async def AtlerEgo(ctx: agents.JobContext):
    groq_client = AsyncOpenAI(
        api_key=os.getenv("GROQ_API_KEY"),
        base_url="https://api.groq.com/openai/v1",
    )
    
    assistant = AgentSession(
        vad = silero.VAD.load(),
        stt=deepgram.STT(
            model="nova-3",
            language="en",
            interim_results=True,
            smart_format=False,  
            endpointing_ms=300),
               
        llm=openai.LLM(
            model="openai/gpt-oss-20b",
            client=groq_client,
            temperature=0.0,
            extra_body={"reasoning_format": "hidden"},
        ),
        
        tts=inference.TTS(
        model="cartesia",
        voice="79a125e8-cd45-4c13-8a67-188112f4dd22",
    ),
        
        turn_handling=TurnHandlingOptions(
            turn_detection="vad",
            endpointing={
                "mode": "fixed",
                "min_delay": 0.3,
                "max_delay": 1.0,
            },
            preemptive_generation={
                "enabled": True,
                "preemptive_tts": False,
            },
        ),
    )
    @assistant.on("conversation_item_added")
    
    def on_conversation_item(event):
        if getattr(event.item, "role", None) == "assistant" and getattr(event.item, "text_content", None):
            text = event.item.text_content.strip()
            asyncio.create_task(
                ctx.room.local_participant.publish_data(
                    text.encode("utf-8"),
                    reliable=True,
                    topic="hud_text"
                )
            )
            
    @assistant.on("user_input_transcribed")
    def on_transcript(ev):
        print(f"[STT] final={ev.is_final} text={ev.transcript!r}")
            
    @assistant.on("user_state_changed")
    def on_user_state(ev):
        print(f"[VAD] {ev.old_state} -> {ev.new_state}")
    
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