import argparse
import json
import time
import sys
import os
import wave

from backend.services.emotion.acoustic import DashScopeAcousticEmotionClient
from backend.services.emotion.models import EmotionLabel, EmotionSource, EmotionSignal
from backend.services.emotion.fusion import deterministic_fusion

def validate_wav(path):
    if not os.path.exists(path):
        return False
    try:
        with wave.open(path, 'rb') as f:
            if f.getnchannels() < 1:
                return False
        return True
    except:
        return False

def calculate_metrics(y_true, y_pred):
    labels = set(y_true + y_pred)
    metrics = {}
    for label in labels:
        tp = sum(1 for yt, yp in zip(y_true, y_pred) if yt == label and yp == label)
        fp = sum(1 for yt, yp in zip(y_true, y_pred) if yt != label and yp == label)
        fn = sum(1 for yt, yp in zip(y_true, y_pred) if yt == label and yp != label)
        
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        
        metrics[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1
        }
        
    macro_f1 = sum(m["f1"] for m in metrics.values()) / len(metrics) if metrics else 0.0
    return {"per_class": metrics, "macro_f1": macro_f1}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    
    if not os.path.exists(args.manifest):
        print(f"Manifest not found: {args.manifest}")
        sys.exit(1)
        
    client = DashScopeAcousticEmotionClient()
    
    results = []
    y_true = []
    y_pred = []
    latencies = []
    high_confidence_errors = []
    
    with open(args.manifest, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            try:
                entry = json.loads(line)
            except:
                continue
                
            wav_path = entry.get("wav")
            expected_label = entry.get("label")
            text = entry.get("text", "")
            
            if not validate_wav(wav_path):
                print(f"Invalid WAV file: {wav_path}")
                continue
                
            with open(wav_path, 'rb') as wf:
                pcm_data = wf.read()
                
            start_time = time.time()
            acoustic_result = client.analyze(pcm_data, soft_deadline_seconds=10.0)
            latency = time.time() - start_time
            latencies.append(latency)
            
            semantic_data = entry.get("semantic")
            if semantic_data:
                semantic_result = EmotionSignal.from_mapping(semantic_data, source=EmotionSource.SEMANTIC)
                final_result = deterministic_fusion(acoustic_result, semantic_result)
            else:
                final_result = acoustic_result
                
            pred_label = final_result.label.value
            y_true.append(expected_label)
            y_pred.append(pred_label)
            
            results.append({
                "wav": wav_path,
                "expected": expected_label,
                "predicted": pred_label,
                "confidence": final_result.confidence,
                "latency": latency
            })
            
            if pred_label != expected_label and final_result.confidence >= 0.8:
                high_confidence_errors.append(results[-1])
                
    metrics = calculate_metrics(y_true, y_pred)
    
    if latencies:
        latencies.sort()
        metrics["latency"] = {
            "p50": latencies[int(len(latencies)*0.5)],
            "p90": latencies[int(len(latencies)*0.9)],
            "p99": latencies[int(len(latencies)*0.99)],
        }
        
    metrics["high_confidence_errors"] = high_confidence_errors
    metrics["total_samples"] = len(y_true)
    
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
        
    print(f"Done. Evaluated {len(y_true)} samples. Macro F1: {metrics.get('macro_f1', 0):.2f}")

if __name__ == "__main__":
    main()
