# Input: User Note (individual line)
# Output: Perplexity summary

import requests
import pandas as pd
import re
import time
#Gemini stuff
import google.generativeai as genai
# merge chunks
from pydub import AudioSegment
import os
from openai import OpenAI

import time
from threading import Lock



GOOGLE_API_KEY= os.environ.get('GOOGLE_API_KEY')
genai.configure(api_key=GOOGLE_API_KEY)

summary_model = genai.GenerativeModel('gemini-1.5-flash') # 15 RPM, $0.030 /million output tokens for higher RPM
script_model = genai.GenerativeModel('gemini-1.5-pro') # 2 RPM, $5 /million output tokens for higher RPM

# implement counters to ensure we are below rate limits
# Global rate-limit counters and locks
summary_model_counter = 0
summary_model_lock = Lock()
script_model_counter = 0
script_model_lock = Lock()

# Timestamps to track last reset
time_last_reset_summary_model = time.time()
time_last_reset_script_model = time.time()

# Rate limit configurations
SUMMARY_MODEL_RPM_LIMIT = 15
SCRIPT_MODEL_RPM_LIMIT = 2


def perplexity(
    user_input: str
) -> dict:
    url = "https://api.perplexity.ai/chat/completions"
    headers = {
        "Authorization": f"Bearer {os.environ.get('PERPLEXITY_API_KEY')}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.1-sonar-small-128k-online",
        "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a research assistant for the user. Provide the user with an in-depth summary of the specified topic, citing scholarly articles and news sources."
                        ),
                    },
                    {   
                        "role": "user",
                        "content": (
                            user_input
                        ),
                    },
                ]
    }
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()

user_topics = ['Neural Dust', 'Quantum Computing', 'Artificial Intelligence']

all_data = pd.DataFrame(user_topics, columns=['topic'])
all_data = all_data.head(3)

for index, row in all_data.iterrows():
    response = perplexity(row['topic'])
    all_data.at[index, 'summary'] = response['choices'][0]['message']['content']
    all_data.at[index, 'citations'] = response['citations']



######## NLP Portion ########

# Function to enforce rate limits
def enforce_rate_limit(model_name):
    global summary_model_counter, script_model_counter
    global time_last_reset_summary_model, time_last_reset_script_model

    current_time = time.time()

    if model_name == "summary_model":
        with summary_model_lock:
            elapsed_time = current_time - time_last_reset_summary_model

            if elapsed_time >= 60:  # Reset every minute
                summary_model_counter = 0
                time_last_reset_summary_model = current_time

            if summary_model_counter >= SUMMARY_MODEL_RPM_LIMIT:
                wait_time = 60 - elapsed_time
                print(f"Rate limit reached for summary_model. Waiting for {wait_time:.2f} seconds.")
                time.sleep(wait_time)
                summary_model_counter = 0
                time_last_reset_summary_model = time.time()

            summary_model_counter += 1

    elif model_name == "script_model":
        with script_model_lock:
            elapsed_time = current_time - time_last_reset_script_model

            if elapsed_time >= 60:  # Reset every minute
                script_model_counter = 0
                time_last_reset_script_model = current_time

            if script_model_counter >= SCRIPT_MODEL_RPM_LIMIT:
                wait_time = 60 - elapsed_time
                print(f"Rate limit reached for script_model. Waiting for {wait_time:.2f} seconds.")
                time.sleep(wait_time)
                script_model_counter = 0
                time_last_reset_script_model = time.time()

            script_model_counter += 1

# Wrap API calls to enforce rate limits
def summarize_with_rate_limit(url):
    enforce_rate_limit("summary_model")
    return summarize(url)

def make_script_with_rate_limit(topics, summaries):
    enforce_rate_limit("script_model")
    return make_script(topics, summaries)


def summarize(url):
    prompt = f"Provide a succinct TLDR summary of the source. Assume the user is familiar with the topic. \
        Highlight the key details that help the user decide whether the source is worth reading. Only include the summary itself;\
        avoid any introductions, explanations, or meta-comments. Make the summary attention-grabbing and informative. {url}"
    
    response = summary_model.generate_content(prompt)
    return response




def make_script(topics, summaries):
    tokens = min(3500, len(topics)*1200)

    system_prompt =f"""
    Act as a professional podcast script writer. Your task is to create a script to be sent to a text-to-speech model where **HOST 1** and **HOST 2** are discussing {len(topics)} topic(s) that the user has requested.
    - When the speakers are referring to each other HOST 1 = Nova and HOST 2 = Atlas
    - Mark the script with **HOST 1** and **HOST 2** for each conversational turn
    - Always maintain the names of the hosts and their order. HOST 1 is always Nova, HOST 2 is always Atlas.
    - Create a sequence, exploring each topic in detail and equally allocating time.
    - These hosts are charismatic and professional. They are excited about the information.
    - Make the conversation at least {tokens} tokens long.
    - Since this is for a text-to-speech model, use short sentences, omit any non-verbal cues, and don't use complex sentences/phrases.
    - Include filler words like 'uh' or repeat words in many of the sentences to make the conversation more natural.
    - Close with 'Thanks for listening to Astra, stay tuned for more episodes.'
    Example: 
    **HOST 1**: Today we have an article about X...
    **HOST 2 **: That's right, Nova. The article discusses Y...
    **HOST 1**: Atlas, how does this article relate to Z?

    Remember: This script must be at least {tokens} tokens long. Do not produce fewer.

Articles: """
    for i in range(len(topics)):
        system_prompt += f" Topic {i} - {topics[i]}: {summaries[i]}"        

    response = script_model.generate_content(system_prompt, 
                                      generation_config = genai.GenerationConfig(
                                        max_output_tokens=tokens+800,
                                        temperature=0.25))
    response = review_script(response, tokens)
    
    return response

def review_script(script, tokens = 3500):
    prompt = f"Review the script for the podcast episode. Refine any wording that may be difficult for a text-to-speech model. Make sure the content flows well and is engaging.\
        Retain the structure, including the conversational turns between the hosts. Remove or replace acronyms where necessary. Do not add any unecessary phrases as this will be fed directly to a text-to-speech model.\
        Script: {script}"
    response = script_model.generate_content(prompt, generation_config = genai.GenerationConfig(
                                        max_output_tokens=tokens+800,
                                        temperature=0.15))
    return response


def generate_script(all_data):
    # generate script
    summaries = all_data['summary'].tolist()
    topics = all_data['topic'].tolist()
    script = make_script_with_rate_limit(topics, summaries)
    print(f'Script: {script.text}')
    return script.text

def generate_email_headers(all_data):
    podcast_description = pd.DataFrame(columns=['title', 'description', 'url'])
    for index, row in all_data.iterrows():
        citations = row['citations']
        for url in citations[0:3]:
            summary = summarize_with_rate_limit(url)
            summary = summary.text
            title = row['topic'] ############################### replace with parsing webpage for title
            # refine text
            ret = summary.replace('\n', ' ')
            # remove \
            ret = ret.replace('\\', '')
            # Ensure no extra spaces
            ret = re.sub(r'\s+', ' ', ret).strip()

            podcast_description = podcast_description.append({'title': title, 'description': ret, 'url': url}, ignore_index=True)
 
    return podcast_description


def clean_text_for_conversational_tts(input_text):
    """
    Cleans text for Text-to-Speech by:
    - Replacing '\n' with spaces.
    - Removing Markdown-style formatting like '**bold**'.
    """
    # Match and extract statements for both HOST 1 and HOST 2.
    pattern = r"HOST [12]([^\n]+)"
    statements = re.findall(pattern, input_text)

    output_text = []
    for statement in statements:
        # Remove (Aria): and (Theo): if exist
        ret = statement.replace('(Atlas):', '').replace('(Nova):', '')

        # Replace '\n' with a space
        ret = ret.replace('\n', ' ')
        # remove '& 1' or '& 2'
        ret = re.sub(r'& \d', '', ret)
        # Remove Markdown-style (*)
        ret = re.sub(r'\*\*\:', '', ret)
        ret = re.sub(r'\*', '', ret)
        # Ensure no extra spaces
        ret = re.sub(r'\s+', ' ', ret).strip()
        
        output_text.append(ret)

    print(output_text)
    return output_text

def create_conversational_podcast(all_data, plan='paid', type='catch_up'):

    def _create_line(client, host, line, num, chunk):
        # $5 of credit here
        # can spend on summarization and script generation

        if host == 1:
            # male
            voice = 'onyx'
        else: # host = 2
            # female
            voice = 'nova'

        output_file = f"data/conversation/{host}/line_{num}_{chunk}.mp3"

         # Ensure the output directory exists
        output_dir = os.path.dirname(output_file)
        os.makedirs(output_dir, exist_ok=True)
        
        try:
            response = client.audio.speech.create(
                model="tts-1",  # Model for TTS (use "tts-1" or "tts-1-hd")
                voice=voice,
                input=line
            )

            # Save the response content (audio) to a file
            with open(output_file, "wb") as audio_file:
                audio_file.write(response.content)

            print(f"Audio file saved as {output_file}")

        except Exception as e:
            print(f"An error occurred: {e}")

    # Instantiates a client
    script = generate_script(all_data)
    turns = clean_text_for_conversational_tts(script)

    turns = turns[:4] # temp for testing

    key = os.environ.get("OPENAI_API_KEY")
    client = OpenAI(api_key=key)

    for index, sentence in enumerate(turns):
        host = 1 if index % 2 == 0 else 2

        # handle lines longer than API limit
        if len(sentence) > 4096:
            # Chunk into 4096 characters
            chunks = [sentence[i:i + 4096] for i in range(0, len(sentence), 4096)]

            for chunk_index, chunk in enumerate(chunks):
                _create_line(client, host, chunk, index, chunk_index)

            chunk_files = [f"data/conversation/{host}/line_{index}_{i}.mp3" for i in range(len(chunks))]
            combined_audio = AudioSegment.empty()
            for file in chunk_files:
                audio_segment = AudioSegment.from_mp3(file)
                combined_audio += audio_segment

            combined_output_file = f"data/conversation/{host}/line_{index}_0.mp3"
            combined_audio.export(combined_output_file, format="mp3")

            # Clean up individual chunk files
            for file in chunk_files:
                os.remove(file)

        else:
            _create_line(client, host, sentence, index, 0)

        time.sleep(60 / 500)  # rate limit = 500 requests per minute

    # merge audio files
    # Create a new AudioSegment object
    final_audio = AudioSegment.from_mp3('data/conversation/1/line_0_0.mp3')
    for i in range(1, len(turns)):
        audio = AudioSegment.from_mp3(f"data/conversation/{1 if i % 2 == 0 else 2}/line_{i}_0.mp3")
        # clean up
        os.remove(f"data/conversation/{1 if i % 2 == 0 else 2}/line_{i}_0.mp3")

        final_audio = final_audio.append(audio)

    # add intro music
    intro_music = AudioSegment.from_file("data/intro_music.mp3")
    final_audio = intro_music.append(final_audio, crossfade=2000)

    # Export the final audio
    final_audio.export("data/conversation/final_conversation.mp3", format="mp3")
    print("Conversation audio file saved as data/conversation/final_conversation.mp3")



# all_data should is up to 3 user topics with title, summary, and citation urls

create_conversational_podcast(all_data) # turns = 4 for testing

email_description = generate_email_headers(all_data)

email_description.to_csv('./data/email_description.csv', index = False)