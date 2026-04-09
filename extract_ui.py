import os
import re
from pathlib import Path

def main():
    studio_path = Path('src/codeingme/studio.py')
    content = studio_path.read_text(encoding='utf-8')
    
    start_marker = 'STUDIO_HTML = """'
    start_idx = content.find(start_marker)
    if start_idx == -1:
        print("Could not find STUDIO_HTML start marker")
        return

    end_idx = content.rfind('"""')
    if end_idx <= start_idx:
        print("Could not find STUDIO_HTML end marker")
        return
        
    html_content = content[start_idx + len(start_marker):end_idx]
    
    # Extract CSS
    css_match = re.search(r'<style>(.*?)</style>', html_content, re.DOTALL)
    css_content = css_match.group(1).strip() if css_match else ''
    
    # Extract JS
    js_match = re.search(r'<script type="module">(.*?)</script>', html_content, re.DOTALL)
    js_content = js_match.group(1).strip() if js_match else ''
    
    # Clean HTML
    clean_html = html_content
    if css_match:
        clean_html = clean_html.replace('<style>' + css_match.group(1) + '</style>', '<link rel="stylesheet" href="/assets/styles.css" />')
    if js_match:
        clean_html = clean_html.replace('<script type="module">' + js_match.group(1) + '</script>', '<script type="module" src="/assets/app.js"></script>')
        
    clean_html = clean_html.strip()
    
    ui_dir = Path('src/codeingme/ui')
    ui_dir.mkdir(parents=True, exist_ok=True)
    
    (ui_dir / 'index.html').write_text(clean_html, encoding='utf-8')
    (ui_dir / 'styles.css').write_text(css_content, encoding='utf-8')
    (ui_dir / 'app.js').write_text(js_content, encoding='utf-8')
    
    print("Extracted HTML, CSS, JS")
    
    new_content = content[:start_idx].strip() + '\n'
    
    # Update imports
    new_content = new_content.replace(
        'from fastapi.responses import HTMLResponse, PlainTextResponse',
        'from fastapi.responses import HTMLResponse, PlainTextResponse, FileResponse\nfrom fastapi.staticfiles import StaticFiles'
    )
    
    # Add mount
    mount_code = """    
    ui_dir = Path(__file__).parent / "ui"
    app.mount("/assets", StaticFiles(directory=str(ui_dir)), name="assets")
"""
    new_content = new_content.replace('app.state.run_manager = manager', 'app.state.run_manager = manager\n' + mount_code)
    
    # Update route
    new_content = new_content.replace(
        'return HTMLResponse(STUDIO_HTML)',
        'return FileResponse(Path(__file__).parent / "ui" / "index.html")'
    )
    
    studio_path.write_text(new_content, encoding='utf-8')
    print("Updated studio.py")

if __name__ == "__main__":
    main()
