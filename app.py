from flask import Flask, render_template, request, jsonify, send_file, g
import os
import yt_dlp
import requests
from urllib.parse import urlparse
import logging
import time
import json
import re
from typing import Dict, Any, Optional, List, Union
from pathlib import Path
import shutil
import sqlite3
import tempfile
import sys
from datetime import datetime

# Set up logging with UTF-8 encoding support
class UTF8StreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            stream.write(msg + self.terminator)
            self.flush()
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        UTF8StreamHandler(),
        logging.FileHandler('downloader.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

class YoutubeDLLogger:
    def debug(self, msg):
        # For compatibility with yt-dlp's debug messages
        if msg.startswith('[debug] '):
            logger.debug(msg[8:])
        else:
            logger.info(msg)

    def info(self, msg):
        logger.info(msg)

    def warning(self, msg):
        logger.warning(msg)

    def error(self, msg):
        logger.error(msg)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 * 1024  # 16GB max file size
DOWNLOAD_FOLDER = 'downloads'

# Create downloads directory if it doesn't exist
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

def is_youtube_url(url):
    youtube_regex = (
        r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/'
        '(watch\?v=|embed/|v/|.+/|\?v=|\&v=|\/v\/|shorts/)?([^\"\&\?\/\s]{11})'
    )
    return bool(re.match(youtube_regex, url))

def is_instagram_url(url):
    instagram_regex = r'https?:\/\/(?:www\.)?instagram\.com\/(?:p\/|reel\/|tv\/|stories\/)[^\s\/]+(?:\/\S*)?'
    return bool(re.match(instagram_regex, url))

def sanitize_filename(filename):
    """
    Sanitize a filename by removing or replacing problematic characters.
    Also handles Unicode characters by transliterating them to ASCII.
    """
    import unicodedata
    
    # Normalize unicode characters (convert accented characters to their base form)
    filename = unicodedata.normalize('NFKD', filename)
    
    # Replace problematic characters with underscores
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1F\x7F]', '_', filename)
    
    # Remove control characters and other problematic unicode
    filename = ''.join(c for c in filename if ord(c) >= 32 or c in (' ', '.', '-', '_'))
    
    # Remove any remaining leading/trailing spaces and dots
    filename = filename.strip('. ')
    
    # Ensure the filename is not empty
    if not filename:
        filename = 'video_' + str(int(time.time()))
        
    return filename

def get_youtube_cookies():
    """Try to get YouTube cookies from browser"""
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
        return extract_cookies_from_browser('chrome')
    except Exception as e:
        logger.warning(f"Could not extract cookies from browser: {str(e)}")
        return {}

def try_download_with_options(ydl_opts, attempt=1, max_attempts=3):
    """Helper function to attempt download with given options"""
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        # Check if file was downloaded
        output_path = ydl.prepare_filename(ydl.extract_info(url, download=False))
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path
            
    except Exception as e:
        logger.warning(f"Attempt {attempt} failed: {str(e)}")
        
    return None

def download_youtube_video(url, quality='best'):
    try:
        logger.info(f"Starting download for URL: {url}")
        
        # Ensure downloads directory exists
        os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
        
        # Get cookies from browser
        cookies = None
        try:
            cookies = get_youtube_cookies()
        except Exception as e:
            logger.warning(f"Could not get YouTube cookies: {str(e)}")
            cookies = {}
        
        # Set up headers to mimic a real browser
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Dnt': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0'
        }
        
        # Set up format selector based on quality
        format_selector = 'best'  # Default to best quality
        
        if quality == 'audio':
            format_selector = 'bestaudio[ext=m4a]/bestaudio/best'
        elif quality == 'best':
            # Best quality with both video and audio
            format_selector = 'bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        elif quality == '4k':
            # 4K resolution (2160p)
            format_selector = 'bestvideo[height<=2160][vcodec^=avc1][fps<=60]+bestaudio[ext=m4a]/best[height<=2160]/best'
        elif quality == '2k':
            # 2K resolution (1440p)
            format_selector = 'bestvideo[height<=1440][vcodec^=avc1][fps<=60]+bestaudio[ext=m4a]/best[height<=1440]/best'
        elif quality == '1080':
            # 1080p resolution with H.264 codec
            format_selector = 'bestvideo[height<=1080][vcodec^=avc1][fps<=60]+bestaudio[ext=m4a]/best[height<=1080]/best'
        elif quality == '720':
            # 720p resolution with H.264 codec
            format_selector = 'bestvideo[height<=720][vcodec^=avc1][fps<=60]+bestaudio[ext=m4a]/best[height<=720]/best'
        elif quality == '480':
            # 480p resolution with H.264 codec
            format_selector = 'bestvideo[height<=480][vcodec^=avc1]+bestaudio[ext=m4a]/best[height<=480]/best'
        
        # Configure yt-dlp options
        ydl_opts = {
            'format': format_selector,
            'outtmpl': os.path.join(DOWNLOAD_FOLDER, '%(title).100s.%(ext)s'),
            'merge_output_format': 'mp4',
            'noplaylist': True,
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'postprocessor_args': [
                '-c:v', 'libx264',  # Force H.264 codec
                '-crf', '18',       # High quality (lower = better quality, 18-28 is good)
                '-preset', 'slow',  # Better compression (slower encoding)
                '-pix_fmt', 'yuv420p',  # Better compatibility
            ],
            'cookiefile': 'cookies.txt' if cookies else None,
            'http_headers': headers,
            'cookies': cookies or {},
            'logger': YoutubeDLLogger(),
            'extractor_retries': 3,
            'retries': 5,
            'fragment_retries': 5,
            'ignoreerrors': False,
            'no_warnings': False,
            'extractor_args': {
                'youtube': {
                    'player_skip': ['webpage'],
                    'skip': ['dash', 'hls'],
                    'player_client': ['android', 'web'],
                    'extract_flat': False,
                },
            },
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'nocheckcertificate': True,
            'quiet': False,
            'verbose': True
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info("Fetching video info...")
            # First get video info
            try:
                info_dict = ydl.extract_info(url, download=False)
                if not info_dict:
                    logger.error("Failed to get video info")
                    return {'success': False, 'error': 'Could not get video information'}
            except Exception as e:
                logger.error(f"Error getting video info: {str(e)}")
                return {'success': False, 'error': f'Failed to get video information: {str(e)}'}
            
            # Get the video title for the filename
            video_title = info_dict.get('title', 'video')
            safe_title = sanitize_filename(video_title)
            
            # Create the output template with sanitized filename
            output_template = os.path.join(DOWNLOAD_FOLDER, f"{safe_title}.%(ext)s")
            
            # Update the options with the new output template
            ydl_opts['outtmpl'] = {'default': output_template}
            
            # Log the output path
            logger.info(f"Output path: {output_template.replace('%(ext)s', 'mp4')}")
            
            # Reinitialize yt-dlp with the updated options
            ydl = yt_dlp.YoutubeDL(ydl_opts)
            
            # Construct the expected filename with .mp4 extension
            filename = os.path.join(DOWNLOAD_FOLDER, f"{safe_title}.mp4")
            
            # Clean up any existing file with the same name
            if os.path.exists(filename):
                try:
                    os.remove(filename)
                    logger.info(f"Removed existing file: {filename}")
                except Exception as e:
                    logger.warning(f"Could not remove existing file {filename}: {str(e)}")
            
            # Download the video
            try:
                ydl.download([url])
            except Exception as e:
                logger.error(f"Download failed: {str(e)}", exc_info=True)
                # Clean up any partial downloads
                part_file = filename + '.part'
                if os.path.exists(part_file):
                    try:
                        os.remove(part_file)
                        logger.info(f"Cleaned up partial download: {part_file}")
                    except Exception as cleanup_error:
                        logger.error(f"Failed to clean up .part file: {str(cleanup_error)}")
                return {'success': False, 'error': f'Download failed: {str(e)}'}
            
            # Verify the file was downloaded
            if os.path.exists(filename) and os.path.getsize(filename) > 1024:  # At least 1KB
                file_size = os.path.getsize(filename)
                basename = os.path.basename(filename)
                logger.info(f"Successfully downloaded: {basename} ({file_size} bytes)")
                
                # Store download information
                if 'downloaded_files' not in globals():
                    global downloaded_files
                    downloaded_files = {}
                    
                downloaded_files[basename] = {
                    'path': filename,
                    'url': url,
                    'title': info_dict.get('title', 'Unknown'),
                    'timestamp': datetime.now().isoformat(),
                    'size': file_size
                }
                
                return {
                    'success': True,
                    'filename': basename,
                    'title': info_dict.get('title', 'Downloaded Video'),
                    'size': file_size
                }
            else:
                # Try to find the file with a similar name (in case extension is different)
                matching_files = [f for f in os.listdir(DOWNLOAD_FOLDER) 
                               if f.startswith(safe_title) and os.path.getsize(os.path.join(DOWNLOAD_FOLDER, f)) > 1024]
                
                if matching_files:
                    actual_filename = matching_files[0]
                    actual_path = os.path.join(DOWNLOAD_FOLDER, actual_filename)
                    file_size = os.path.getsize(actual_path)
                    
                    # Store download information
                    downloaded_files[actual_filename] = {
                        'path': actual_path,
                        'url': url,
                        'title': info_dict.get('title', 'Unknown'),
                        'timestamp': datetime.now().isoformat(),
                        'size': file_size
                    }
                    
                    logger.info(f"Found downloaded file with different name: {actual_filename} ({file_size} bytes)")
                    
                    return {
                        'success': True,
                        'filename': actual_filename,
                        'title': info_dict.get('title', 'Downloaded Video'),
                        'size': file_size
                    }
                
                error_msg = f"Downloaded file not found or too small. Expected: {filename}"
                logger.error(error_msg)
                return {'success': False, 'error': error_msg}
                    
    except Exception as e:
        logger.error(f"Error in download_youtube_video: {str(e)}", exc_info=True)
        return {'success': False, 'error': f'An error occurred: {str(e)}'}

def download_instagram_video(url):
    try:
        # First get video info
        ydl_info = yt_dlp.YoutubeDL({
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }).extract_info(url, download=False)
        
        # Sanitize the title for filename
        safe_title = sanitize_filename(ydl_info.get('title', 'instagram_video'))
        output_template = os.path.join(DOWNLOAD_FOLDER, f'{safe_title}.%(ext)s')
        
        ydl_opts = {
            'format': 'best',
            'outtmpl': output_template,
            'quiet': True,
            'noplaylist': True,
            'ignoreerrors': True,
            'no_warnings': True,
            'extract_flat': False,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Referer': 'https://www.instagram.com/',
                'Origin': 'https://www.instagram.com',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            },
            'extractor_retries': 3,
            'retries': 10,
            'fragment_retries': 10,
            'merge_output_format': 'mp4',
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            return {
                'success': True,
                'filename': os.path.basename(filename),
                'title': info.get('title', 'instagram_video')
            }
    except Exception as e:
        return {'success': False, 'error': str(e)}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    try:
        data = request.json
        url = data.get('url')
        quality = data.get('quality', 'best')  # Default to 'best' if not specified
        
        if not url:
            return jsonify({'success': False, 'error': 'No URL provided'}), 400
            
        if is_youtube_url(url):
            result = download_youtube_video(url, quality=quality)
        elif is_instagram_url(url):
            result = download_instagram_video(url)
        else:
            return jsonify({'success': False, 'error': 'Unsupported URL. Only YouTube and Instagram URLs are supported.'})
        
        if result['success']:
            result['download_url'] = f'/download_file/{result["filename"]}'
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in download route: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download_file/<path:filename>')
def download_file(filename):
    try:
        # Prevent directory traversal and sanitize the filename
        safe_filename = sanitize_filename(os.path.basename(filename))
        file_path = os.path.join(DOWNLOAD_FOLDER, safe_filename)
        
        # Try to find the file with the exact name first
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            # If not found, try to find a file with a similar name
            matching_files = [f for f in os.listdir(DOWNLOAD_FOLDER) 
                           if f.startswith(os.path.splitext(safe_filename)[0])]
            
            if matching_files:
                # Use the first matching file
                safe_filename = matching_files[0]
                file_path = os.path.join(DOWNLOAD_FOLDER, safe_filename)
            else:
                logger.error(f"File not found: {filename}")
                return "File not found", 404
        
        # Get the file's extension to determine content type
        ext = os.path.splitext(safe_filename)[1].lower()
        mime_types = {
            '.mp4': 'video/mp4',
            '.mp3': 'audio/mpeg',
            '.m4a': 'audio/mp4',
            '.webm': 'video/webm',
            '.mkv': 'video/x-matroska',
            '.part': 'application/octet-stream',
        }
        
        # Default to octet-stream if extension not recognized
        mime_type = mime_types.get(ext, 'application/octet-stream')
        
        # Log the download attempt
        logger.info(f"Serving file: {safe_filename} (original: {filename})")
        
        # Use send_file to serve the file with proper headers
        return send_file(
            file_path,
            as_attachment=True,
            download_name=safe_filename,
            mimetype=mime_type,
            etag=False  # Disable etag to prevent caching issues
        )
        
    except Exception as e:
        logger.error(f"Error downloading file {filename}: {str(e)}", exc_info=True)
        return f"Error downloading file: {str(e)}", 500

def check_yt_dlp_version():
    """Check if yt-dlp is up to date"""
    try:
        import subprocess
        import sys
        import re
        
        # Get current version
        import yt_dlp
        current_version = yt_dlp.version.__version__
        logger.info(f"Current yt-dlp version: {current_version}")
        
        # Update to latest version
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp'])
        
        # Get new version
        import importlib
        importlib.reload(yt_dlp)
        new_version = yt_dlp.version.__version__
        
        if new_version != current_version:
            logger.info(f"Updated yt-dlp from {current_version} to {new_version}")
        else:
            logger.info("yt-dlp is already up to date")
            
    except Exception as e:
        logger.error(f"Failed to update yt-dlp: {str(e)}")
        logger.info("Trying to continue with current version...")

if __name__ == '__main__':
    # Ensure yt-dlp is up to date
    check_yt_dlp_version()
    
    # Create a test downloads directory
    os.makedirs('downloads', exist_ok=True)
    
    # Start the application
    app.run(debug=True, host='0.0.0.0', port=5000)
