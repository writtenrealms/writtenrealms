import os

from openai import OpenAI


class GPT:

    def __init__(self):
        GPT_PROJECT_ID = os.getenv("GPT_PROJECT_ID")
        GPT_SECRET_KEY = os.getenv("GPT_SECRET_KEY")

        if not GPT_PROJECT_ID or not GPT_SECRET_KEY:
            raise ValueError("GPT_PROJECT_ID and GPT_SECRET_KEY must be set")

        client = OpenAI(api_key=GPT_SECRET_KEY, project=GPT_PROJECT_ID)
        self.client = client

    def chat(self, messages, model="gpt-4o-mini"):
        completion = self.client.chat.completions.create(model=model, messages=messages)
        response = completion.choices[0].message.content
        return response
