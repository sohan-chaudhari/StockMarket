from bs4 import BeautifulSoup
import re

def analyze():
    print("Reading debug_output.txt...")
    with open("debug_output.txt", "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, 'html.parser')
    
    # Remove scripts and styles to avoid false positives
    for script in soup(["script", "style"]):
        script.decompose()

    print("Analyzing structure...")
    with open("analysis.txt", "w", encoding="utf-8") as out:
        # 1. Find the main price to confirm we are in the right area
        # The main price usually has a big font or specific class like 'YMlKec' (from previous context)
        # but let's just look for the text structure generally.
        
        # 2. Search for "Open", "High", "Low", "Prev Close" strictly
        labels = ["Open", "High", "Low", "Previous close", "Day Range", "Year Range"]
        
        for label_text in labels:
            out.write(f"\n=== SEARCHING FOR: '{label_text}' ===\n")
            # Find all matches
            matches = soup.find_all(string=lambda text: text and label_text in text)
            
            for i, match in enumerate(matches):
                if len(match) > 50: # Skip long unrelated text
                    continue
                
                out.write(f"Match #{i+1}: '{match.strip()}'\n")
                parent = match.parent
                out.write(f"  Parent: {parent.name} | Class: {parent.get('class')} | ID: {parent.get('id')}\n")
                
                # Check siblings (value is often a sibling)
                out.write("  Siblings:\n")
                for sib in parent.next_siblings:
                    if sib.name:
                        out.write(f"    <{sib.name}> Class: {sib.get('class')} | Text: {sib.get_text(strip=True)}\n")
                
                # Check parent's siblings (sometimes label and value are cousins)
                if parent.parent:
                    out.write(f"  Parent's Parent: {parent.parent.name} | Class: {parent.parent.get('class')}\n")
                    out.write("  Parent's Siblings:\n")
                    for sib in parent.parent.next_siblings:
                        if sib.name:
                            out.write(f"    <{sib.name}> Class: {sib.get('class')} | Text: {sib.get_text(strip=True)}\n")
                out.write("-" * 30 + "\n")

    print("Analysis complete. Written to analysis.txt")

if __name__ == "__main__":
    analyze()
