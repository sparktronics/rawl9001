#!/usr/bin/env python3
"""Quick test to verify Vertex AI / Gemini access using the new Google GenAI SDK."""

from google import genai

# Initialize the GenAI client for Vertex AI
# This automatically uses Application Default Credentials (ADC)
client = genai.Client(
    vertexai=True,  # Use Vertex AI backend
    project="rawl-extractor",  # Replace with your project
    location="europe-west1",
)

# Generate content using the new SDK
response = client.models.generate_content(
    model="gemini-2.0-flash-001",
    contents="Say 'Hello RAWL 9001!' and nothing else.",
)

print("✅ Google GenAI SDK connection successful!")
print(f"Response: {response.text}")
