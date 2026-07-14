import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer
from typing import Dict
import os


class FinBERTModel:
    """
    FinBERT sentiment model (Model 1 - Base Signal)
    """
    
    def __init__(self, model_name: str = "ProsusAI/finbert"):
        self.model_name = model_name
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        
        print(f"Loading FinBERT model: {model_name}")
        print(f"Using device: {self.device}")
        
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name)
        self.model.to(self.device)
        self.model.eval()
        
        print("FinBERT model loaded successfully!")
    
    def predict(self, text: str) -> Dict:
        """
        Predict sentiment using FinBERT
        Returns S1 score ∈ [-1, 1]
        """
        # Tokenize (max 512 tokens for BERT)
        inputs = self.tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True
        ).to(self.device)
        
        # Inference
        with torch.no_grad():
            outputs = self.model(**inputs)
            logits = outputs.logits
            probabilities = torch.nn.functional.softmax(logits, dim=1)[0]
        
        # FinBERT outputs: [negative, neutral, positive]
        neg_prob = probabilities[0].item()
        neu_prob = probabilities[1].item()
        pos_prob = probabilities[2].item()
        
        # Convert to S1 score: positive - negative
        s1_score = pos_prob - neg_prob
        
        return {
            "score": s1_score,  # S1 ∈ [-1, 1]
            "probabilities": {
                "negative": neg_prob,
                "neutral": neu_prob,
                "positive": pos_prob
            }
        }
