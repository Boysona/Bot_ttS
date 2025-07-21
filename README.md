---

# Text-to-Speech Bot

This Telegram bot, named **Text-to-Speech Bot**, transforms your written messages into natural-sounding audio using advanced AI voice synthesis. Simply send text, and the bot will convert it into an audio file, allowing you to listen to your messages or share them with others.

## Key Features

* **Text-to-Speech Conversion:** Convert any text message into high-quality, realistic speech.
* **Multiple Languages & Voices:** Choose from a wide array of languages and distinct AI voices to customize the audio output.
* **Voice Customization:** Adjust the **pitch** (how high or low the voice sounds) and **rate** (how fast or slow the voice speaks) to fine-tune your audio.
* **User-Friendly Commands:** Easy-to-use commands like `/start`, `/voice`, `/pitch`, and `/rate` make interaction seamless.
* **Privacy-Focused:** Your text input and generated audio are processed in real-time and **not stored** by the bot. Only your chosen voice preferences and basic activity statistics are saved to improve service.

---

# README for Text-to-Speech Bot

This document outlines the setup and deployment instructions for the Text-to-Speech Telegram Bot.

## Bot Description

The **Text-to-Speech Bot** is a Telegram bot that leverages Microsoft Cognitive Services Speech API to convert text messages into audio. Users can select from various languages and voices, and even customize the pitch and rate of the synthesized speech. The bot is designed with privacy in mind, ensuring that user text input and generated audio are not stored.

## Features

* **Text-to-Speech:** Convert text messages into audio files.
* **Multi-language Support:** Choose from a wide range of languages.
* **Voice Selection:** Select different AI voices within each language.
* **Pitch Control:** Adjust the pitch of the synthesized voice.
* **Rate Control:** Control the speaking speed of the voice.
* **Admin Panel (for specified ADMIN\_ID):**
    * View bot uptime and statistics.
    * Broadcast messages to all users.
    * See total registered users.
* **Subscription Gate:** Optionally require users to join a specified Telegram channel to use the bot.
* **Privacy Notice:** Provides clear information about data handling.

## Technologies Used

* **Python 3.9+**
* **`pyTelegramBotAPI`**: For Telegram Bot API interaction.
* **`msspeech`**: Python wrapper for Microsoft Cognitive Services Speech SDK.
* **`Flask`**: For handling webhooks.
* **`pymongo`**: MongoDB driver for Python.
* **`MongoDB Atlas`**: Cloud database for user settings and statistics.
* **`Render.com` (or similar PaaS):** For deployment.

## Setup and Installation

### 1. Prerequisites

* **Telegram Bot Token:** Obtain a new bot token from BotFather on Telegram.
* **MongoDB Atlas Cluster:** Set up a free-tier MongoDB Atlas cluster.
    * Create a database user with read and write access.
    * Allow access from `0.0.0.0/0` (for Render deployment) or specific IP addresses.
* **Microsoft Azure Account:** While `msspeech` doesn't strictly require an Azure key for basic use (it might use a public endpoint), for robust and scalable TTS, you would typically use Azure Cognitive Services Speech. For this specific implementation, `msspeech` handles the underlying API calls.
* **Render Account:** For deploying the Flask application as a web service.

### 2. Environment Variables

Create a `.env` file or set these environment variables directly in your deployment platform (e.g., Render):

* `TOKEN`: Your Telegram Bot API token.
* `ADMIN_ID`: Your Telegram User ID (numeric) for admin access.
* `WEBHOOK_URL`: The URL of your deployed application (e.g., `https://your-app-name.onrender.com`).
* `MONGO_URI`: Your MongoDB Atlas connection string.
* `PORT`: The port your Flask app will listen on (Render typically sets this, often `8080`).

Example `.env` file:


TOKEN=YOUR_TELEGRAM_BOT_TOKEN_HERE
ADMIN_ID=YOUR_TELEGRAM_USER_ID
WEBHOOK_URL=https://your-render-app-name.onrender.com
MONGO_URI="mongodb+srv://user:pass@cluster.mongodb.net/mydb?retryWrites=true&w=majority"
PORT=8080

### 3. Clone the Repository

```bash
git clone <repository_url>
cd text-to-speech-bot

4. Install Dependencies
pip install -r requirements.txt

Create requirements.txt if it doesn't exist, with the following content:
pyTelegramBotAPI
Flask
python-msspeech
pymongo
dnspython # Required by pymongo for SRV records in Atlas URI

5. MongoDB Setup (Automatic)
The connect_to_mongodb() function in main.py handles the initial connection and creation of necessary collections and indexes (users, tts_users, tts_processing_stats) upon bot startup. It also loads existing user data into in-memory caches.
6. Deployment (e.g., Render)
 * Create a New Web Service on Render:
   * Connect your GitHub repository.
   * Set the Build Command to: pip install -r requirements.txt
   * Set the Start Command to: python main.py
   * Add the Environment Variables (from step 2).
   * Ensure the PORT variable is set (Render typically sets this automatically).
 * Set Webhook:
   After deployment, visit YOUR_WEBHOOK_URL/set_webhook in your browser. This will register your deployed application's URL with Telegram as the webhook endpoint for your bot.
   Example: https://your-render-app-name.onrender.com/set_webhook
Usage
 * Start the Bot: Send /start to your bot on Telegram.
 * Select Voice Language: Use the /voice command to choose a language and then a specific voice from the inline keyboard.
 * Adjust Voice Settings (Optional):
   * Use /pitch to modify the voice's pitch.
   * Use /rate to change the voice's speaking speed.
 * Send Text: Type and send any text message. The bot will reply with an audio file of your text.
Admin Commands (for ADMIN_ID only)
 * /start: Access the admin panel.
 * Send Broadcast: Send a message to all bot users.
 * Total Users: Get the count of registered users.
 * /status: View detailed bot statistics (uptime, conversion counts, processing times).
Privacy Policy
This bot prioritizes your privacy.
 * Text Input & Audio Files: Text sent for conversion is processed in real-time and not stored. The generated audio files are temporary and deleted immediately after being sent to you.
 * User Preferences & Activity: Your Telegram User ID, chosen voice preferences (voice, pitch, and rate), and basic activity data (last active timestamp, TTS conversion count) are stored in MongoDB. This data is used solely to remember your settings and for anonymous, aggregated statistics to improve the service.
 * No Sharing: Your personal data or text input is not shared with any third parties. Text-to-speech conversion is facilitated via Microsoft Cognitive Services Speech API, but we ensure your data is not stored by us after processing.
 * Data Retention: User IDs and voice preferences are stored to support your settings. You can cease using the bot or contact the administrator for explicit data deletion.
For any questions or concerns, contact the bot administrator at @boysona.


