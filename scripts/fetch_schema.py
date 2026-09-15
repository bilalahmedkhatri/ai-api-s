import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

try:
    req = urllib.request.Request("https://generativelanguage.googleapis.com/$discovery/rest?version=v1alpha")
    with urllib.request.urlopen(req, context=ctx) as response:
        doc = json.loads(response.read().decode())
        
        print("--- SpeechConfig ---")
        print(json.dumps(doc.get('schemas', {}).get('SpeechConfig', {}), indent=2))
        
        print("\n--- VoiceConfig ---")
        print(json.dumps(doc.get('schemas', {}).get('VoiceConfig', {}), indent=2))
        print("--- PrebuiltVoiceConfig ---")
        print(json.dumps(doc.get('schemas', {}).get('PrebuiltVoiceConfig', {}), indent=2))
        
        print("\n--- MultiSpeakerVoiceConfig ---")
        print(json.dumps(doc.get('schemas', {}).get('MultiSpeakerVoiceConfig', {}), indent=2))
except Exception as e:
    print(f"Error: {e}")
