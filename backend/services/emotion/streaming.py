import json
from backend.services.emotion.models import EmotionSignal, EmotionSource

def extract_user_emotion(json_buffer: str) -> EmotionSignal | None:
    marker = '"user_emotion"'
    start_idx = json_buffer.find(marker)
    if start_idx == -1:
        return None
        
    # Find the opening brace after the marker
    brace_idx = json_buffer.find('{', start_idx + len(marker))
    if brace_idx == -1:
        return None
        
    depth = 0
    in_string = False
    escape = False
    end_idx = -1
    
    for i in range(brace_idx, len(json_buffer)):
        c = json_buffer[i]
        
        if escape:
            escape = False
            continue
            
        if c == '\\':
            escape = True
            continue
            
        if c == '"':
            in_string = not in_string
            continue
            
        if not in_string:
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break
                    
    if end_idx != -1:
        obj_str = json_buffer[brace_idx:end_idx+1]
        try:
            mapping = json.loads(obj_str)
            return EmotionSignal.from_mapping(mapping, source=EmotionSource.SEMANTIC)
        except json.JSONDecodeError:
            pass
            
    return None
