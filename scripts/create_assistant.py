from openai import OpenAI

client = OpenAI()

asst = client.beta.assistants.create(
    name="DiscordBot",
    instructions=(
        "Discord でカジュアルに日本語回答。"
        "必要なら web 検索やコードを書いて実行してから答える。"
    ),
    model="gpt-4o",
    tools=[
        {"type": "web_search"},
        {"type": "code_interpreter"},
    ],
)
print(asst.id)

