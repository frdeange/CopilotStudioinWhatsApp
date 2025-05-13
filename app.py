import httpx
import json
import threading
import os
import websocket

from azure.communication.messages import NotificationMessagesClient
from azure.communication.messages.models import TextNotificationContent
from dotenv import load_dotenv
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

load_dotenv()
app = FastAPI()

# DirectLine configuration
DIRECTLINE_URL = os.getenv('DIRECTLINE_BASE_URL')
if not DIRECTLINE_URL:
    raise RuntimeError('Environment variable DIRECTLINE_BASE_URL is not defined. Please add it to your environment or .env file.')
DIRECT_LINE_SECRET = os.getenv('DIRECT_LINE_SECRET')
if not DIRECT_LINE_SECRET:
    raise RuntimeError('Environment variable DIRECT_LINE_SECRET is not defined. Please add it to your environment or .env file.')

# ACS configuration
ACS_CONNECTION_STRING = os.getenv('ACS_CONNECTION_STRING')
if not ACS_CONNECTION_STRING:
    raise RuntimeError('Environment variable ACS_CONNECTION_STRING is not defined. Please add it to your environment or .env file.')

# WhatsApp Channel Registration ID
def get_whatsapp_channel_id():
    channel_id = os.getenv('WHATSAPP_CHANNEL_ID')
    if not channel_id:
        raise RuntimeError('Environment variable WHATSAPP_CHANNEL_ID is not defined. Please add it to your environment or .env file.')
    return channel_id

# Utility to start a DirectLine conversation
async def start_directline_conversation():
    headers = {'Authorization': f'Bearer {DIRECT_LINE_SECRET}'}
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{DIRECTLINE_URL}/conversations", headers=headers)
        if response.status_code == 201:
            data = response.json()
            return data['conversationId'], data['token'], data['streamUrl']
    return None, None, None

# Utility to send a message to DirectLine
async def send_message_to_directline(conversation_id, token, text):
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    payload = {
        'type': 'message',
        'from': {'id': 'whatsapp-user'},
        'text': text
    }
    url = f"{DIRECTLINE_URL}/conversations/{conversation_id}/activities"
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code in (200, 201):
            return response.json().get('id')
    return None

# Utility to get bot response using DirectLine WebSocket
def get_bot_response_ws(stream_url, token, timeout=30):
    """
    Waits for the bot response using DirectLine WebSocket.
    Returns the response text or None if timeout.
    """
    result = {'text': None}
    def on_message(ws, message):
        data = json.loads(message)
        activities = data.get('activities', [])
        for activity in activities:
            if activity.get('from', {}).get('role') == 'bot' and activity.get('type') == 'message':
                result['text'] = activity.get('text')
                ws.close()
    def on_error(ws, error):
        ws.close()
    def on_close(ws, close_status_code, close_msg):
        pass
    headers = [f'Authorization: Bearer {token}']
    ws = websocket.WebSocketApp(stream_url,
        header=headers,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close)
    wst = threading.Thread(target=ws.run_forever)
    wst.daemon = True
    wst.start()
    import time
    start = time.time()
    while time.time() - start < timeout:
        if result['text']:
            break
        time.sleep(0.5)
    ws.close()
    return result['text']

# Utility to send WhatsApp message via ACS using the official SDK
def send_whatsapp_message_acs_sdk(to_number, message_text):
    channel_id = get_whatsapp_channel_id()
    if not ACS_CONNECTION_STRING or not channel_id:
        return 500, 'Missing environment variables for ACS'
    try:
        client = NotificationMessagesClient.from_connection_string(ACS_CONNECTION_STRING)
        text_content = TextNotificationContent(
            channel_registration_id=channel_id,
            to=[to_number],
            content=message_text
        )
        result = client.send(text_content)
        receipt = result.receipts[0] if result.receipts else None
        if receipt:
            return 202, f"MessageId: {receipt.message_id}, To: {receipt.to}"
        else:
            return 500, 'No receipt returned'
    except Exception as e:
        return 500, str(e)
    

# Endpoint to just test the server is running
@app.get('/')
async def root():
    return {'message': 'Hello! Your server is running and ready to receive messagges from ACS.'}

# Endpoint to receive WhatsApp messages from ACS
@app.post('/webhook/whatsapp')
async def whatsapp_webhook(request: Request):
    try:
        events = await request.json()
        if isinstance(events, list):
            handled = False
            for event in events:
                # Subscription validation from Event Grid
                if event.get('eventType') == 'Microsoft.EventGrid.SubscriptionValidationEvent':
                    validation_code = event['data']['validationCode']
                    return JSONResponse({'validationResponse': validation_code}, status_code=200)
                
                # Actual WhatsApp message
                if event.get('eventType') == 'Microsoft.Communication.AdvancedMessageReceived':
                    message_text = event['data'].get('content')
                    from_number = event['data'].get('from')
                    
                    # Start DirectLine conversation and get streamUrl
                    conversation_id, token, stream_url = await start_directline_conversation()
                    if not conversation_id:
                        return JSONResponse({'error': 'Failed to start DirectLine conversation'}, status_code=500)
                    
                    # Send user message
                    message_id = await send_message_to_directline(conversation_id, token, message_text)
                    if not message_id:
                        return JSONResponse({'error': 'Failed to send message to DirectLine'}, status_code=500)
                    
                    # Wait for bot response via WebSocket (remains sync/threaded)
                    bot_response = get_bot_response_ws(stream_url, token, timeout=60)
                    if not bot_response:
                        bot_response = 'No response from bot.'
                    acs_status, acs_response = send_whatsapp_message_acs_sdk(from_number, bot_response)
                    return JSONResponse({'to': from_number, 'text': bot_response, 'acs_status': acs_status, 'acs_response': acs_response}, status_code=200)
                
                # Message delivery status (just log and return 200 OK)
                if event.get('eventType') == 'Microsoft.Communication.AdvancedMessageDeliveryStatusUpdated':
                    handled = True
                    continue
            
            # If only delivery status events, respond 200 OK
            if handled:
                return JSONResponse({}, status_code=200)
        
        # If not a valid event
        return JSONResponse({'error': 'Unsupported event type'}, status_code=400)
    except Exception as e:
        return JSONResponse({'error': f'Exception: {str(e)}'}, status_code=400)
