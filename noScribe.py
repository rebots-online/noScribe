# noScribe - AI-powered Audio Transcription
# Copyright (C) 2023 Kai Dröge
# ported to MAC by Philipp Schneider (gernophil)

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.


import tkinter as tk
import customtkinter as ctk
from tkHyperlinkManager import HyperlinkManager
import webbrowser
from functools import partial
from PIL import Image
import os
import platform
import yaml
import locale
import appdirs
from subprocess import run, Popen, PIPE, STDOUT
if platform.system() == 'Windows':
    from subprocess import STARTUPINFO, STARTF_USESHOWWINDOW
import re
# from pyannote.audio import Pipeline (> imported on demand below)
if platform.system() == "Darwin": # = MAC
    if platform.machine() == "x86_64":
        os.environ['KMP_DUPLICATE_LIB_OK']='True' # prevent OMP: Error #15: Initializing libomp.dylib, but found libiomp5.dylib already initialized.
    # if platform.machine() == "arm64": # Intel should also support MPS
    if platform.mac_ver()[0] >= '12.3': # MPS needs macOS 12.3+
        os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = str(1)
from faster_whisper import WhisperModel
import AdvancedHTMLParser
from typing import Any, Mapping, Optional, Text
import sys
from itertools import islice
from threading import Thread
from queue import Queue, Empty
from tempfile import TemporaryDirectory
import datetime
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from io import StringIO
from elevate import elevate
if platform.system() == 'Windows':
    import cpufeature
if platform.system() == "Darwin": # = MAC
    import shlex
    import Foundation
  
import logging

logging.basicConfig()
logging.getLogger("faster_whisper").setLevel(logging.DEBUG)

app_version = '0.3'
app_dir = os.path.abspath(os.path.dirname(__file__))
ctk.set_appearance_mode('dark')
ctk.set_default_color_theme('blue')

default_html = """
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" "http://www.w3.org/TR/REC-html40/strict.dtd">
<html >
<head >
<meta charset="UTF-8" />
<meta name="qrichtext" content="1" />
<style type="text/css" >
p, li { white-space: pre-wrap; }
</style>
<style type="text/css" >
 a { text-decoration: none; color: #000000; } 
 p { font-size: 0.9em; } 
 .MsoNormal { font-family: "Arial"; font-weight: 400; font-style: normal; font-size: 0.9em; }
 @page WordSection1 {mso-line-numbers-restart: continuous; mso-line-numbers-count-by: 1; mso-line-numbers-start: 1; }
 div.WordSection1 {page:WordSection1;} 
</style>
</head>
<body style="font-family: 'Arial'; font-weight: 400; font-style: normal" >
</body>
</html>"""

# config
config_dir = appdirs.user_config_dir('noScribe')
if not os.path.exists(config_dir):
    os.makedirs(config_dir)
try:
    with open(f'{config_dir}/config.yml', 'r') as file:
        config = yaml.safe_load(file)
except: # seems we run it for the first time and there is no config file
    config = {}

def save_config():
    with open(f'{config_dir}/config.yml', 'w') as file:
        yaml.safe_dump(config, file)

# locale: setting the language of the UI
# see https://pypi.org/project/python-i18n/
import i18n
from i18n import t
i18n.set('filename_format', '{locale}.{format}')
i18n.load_path.append(os.path.join(app_dir, 'trans'))

try:
    app_locale = config['locale']
except:
    app_locale = 'auto'

if app_locale == 'auto': # read system locale settings
    try:
        if platform.system() == 'Windows':
            app_locale = locale.getdefaultlocale()[0][0:2]
        elif platform.system() == "Darwin": # = MAC
            app_locale = Foundation.NSUserDefaults.standardUserDefaults().stringForKey_('AppleLocale')[0:2]
    except:
        app_locale = 'en'
i18n.set('fallback', 'en')
i18n.set('locale', app_locale)

# Check CPU capabilities and select the right version of whisper
if platform.system() == 'Windows':
    if cpufeature.CPUFeature["AVX2"] == True and cpufeature.CPUFeature["OS_AVX"] == True:
        whisper_path = os.path.join(app_dir, "whisper_avx2")
    else:
        whisper_path = os.path.join(app_dir, "whisper_sse2")
elif platform.system() == "Darwin": # = MAC
    if platform.machine() == "arm64":
        whisper_path = os.path.join(app_dir, "whisper_mac_arm64")
    elif platform.machine() == "x86_64":
        whisper_path = os.path.join(app_dir, "whisper_mac_x86_64")
    else:
        raise Exception('Could not detect Apple architecture.')
else:
    raise Exception('Platform not supported yet.')

# timestamp regex
timestamp_re = re.compile('\[\d\d:\d\d:\d\d.\d\d\d --> \d\d:\d\d:\d\d.\d\d\d\]')

# Helper functions

def millisec(timeStr): # convert 'hh:mm:ss' string to milliseconds
    try:
        spl = timeStr.split(':')
        s = (int)((int(spl[0]) * 60 * 60 + int(spl[1]) * 60 + float(spl[2]) )* 1000)
        return s
    except:
        raise Exception(t('err_invalid_time_string', time = timeStr))

def iter_except(function, exception):
        # Works like builtin 2-argument `iter()`, but stops on `exception`.
        try:
            while True:
                yield function()
        except exception:
            return


class TimeEntry(ctk.CTkEntry): # special Entry box to enter time in the format hh:mm:ss
                               # based on https://stackoverflow.com/questions/63622880/how-to-make-python-automatically-put-colon-in-the-format-of-time-hhmmss
    def __init__(self, master, **kwargs):
        ctk.CTkEntry.__init__(self, master, **kwargs)
        vcmd = self.register(self.validate)

        self.bind('<Key>', self.format)
        self.configure(validate="all", validatecommand=(vcmd, '%P'))

        self.valid = re.compile('^\d{0,2}(:\d{0,2}(:\d{0,2})?)?$', re.I)

    def validate(self, text):
        if text == '':
            return True
        elif ''.join(text.split(':')).isnumeric():
            return not self.valid.match(text) is None
        else:
            return False

    def format(self, event):
        if event.keysym not in ['BackSpace', 'Shift_L', 'Shift_R', 'Control_L', 'Control_R']:
            i = self.index('insert')
            if i in [2, 5]:
                if event.char != ':':
                    if self.get()[i:i+1] != ':':
                        self.insert(i, ':')

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.audio_file = ''
        self.transcript_file = ''
        self.log_file = None
        self.cancel = False # if set to True, transcription will be canceled

        # configure window
        self.title('noScribe - ' + t('app_header'))
        self.geometry(f"{1100}x{650}")
        self.iconbitmap('noScribeLogo.ico')

        # header
        self.frame_header = ctk.CTkFrame(self, height=100)
        self.frame_header.pack(padx=0, pady=0, anchor='nw', fill='x')

        self.frame_header_logo = ctk.CTkFrame(self.frame_header, fg_color='transparent')
        self.frame_header_logo.pack(anchor='w', side='left')

        # logo
        self.logo_label = ctk.CTkLabel(self.frame_header_logo, text="noScribe", font=ctk.CTkFont(size=42, weight="bold"))
        self.logo_label.pack(padx=20, pady=[40, 0], anchor='w')

        # sub header
        self.header_label = ctk.CTkLabel(self.frame_header_logo, text=t('app_header'), font=ctk.CTkFont(size=16, weight="bold"))
        self.header_label.pack(padx=20, pady=[0, 20], anchor='w')
     
        # graphic
        self.header_graphic = ctk.CTkImage(dark_image=Image.open(os.path.join(app_dir, 'graphic_sw.png')), size=(926,119))
        self.header_graphic_label = ctk.CTkLabel(self.frame_header, image=self.header_graphic, text='')
        self.header_graphic_label.pack(anchor='ne', side='right', padx=[30,30])

        # main window
        self.frame_main = ctk.CTkFrame(self)
        self.frame_main.pack(padx=0, pady=0, anchor='nw', expand=True, fill='both')

        # create sidebar frame for options
        self.sidebar_frame = ctk.CTkFrame(self.frame_main, width=270, corner_radius=0, fg_color='transparent')
        self.sidebar_frame.pack(padx=0, pady=0, fill='y', expand=False, side='left')

        # input audio file
        self.label_audio_file = ctk.CTkLabel(self.sidebar_frame, text=t('label_audio_file'))
        self.label_audio_file.pack(padx=20, pady=[20,0], anchor='w')

        self.frame_audio_file = ctk.CTkFrame(self.sidebar_frame, width=250, height=33, corner_radius=8, border_width=2)
        self.frame_audio_file.pack(padx=20, pady=[0,10], anchor='w')
        
        self.label_audio_file_name = ctk.CTkLabel(self.frame_audio_file, width=200, corner_radius=8, anchor='w', text=t('label_audio_file_name'))
        self.label_audio_file_name.place(x=3, y=3)

        self.button_audio_file = ctk.CTkButton(self.frame_audio_file, width=45, height=29, text='📂', command=self.button_audio_file_event)
        self.button_audio_file.place(x=203, y=2)

        # input transcript file name
        self.label_transcript_file = ctk.CTkLabel(self.sidebar_frame, text=t('label_transcript_file'))
        self.label_transcript_file.pack(padx=20, pady=[10,0], anchor='w')

        self.frame_transcript_file = ctk.CTkFrame(self.sidebar_frame, width=250, height=33, corner_radius=8, border_width=2)
        self.frame_transcript_file.pack(padx=20, pady=[0,10], anchor='w')
        
        self.label_transcript_file_name = ctk.CTkLabel(self.frame_transcript_file, width=200, corner_radius=8, anchor='w', text=t('label_transcript_file_name'))
        self.label_transcript_file_name.place(x=3, y=3)

        self.button_transcript_file = ctk.CTkButton(self.frame_transcript_file, width=45, height=29, text='📂', command=self.button_transcript_file_event)
        self.button_transcript_file.place(x=203, y=2)

        # Options grid
        self.frame_options = ctk.CTkFrame(self.sidebar_frame, width=250, fg_color='transparent')
        self.frame_options.pack(padx=20, pady=10, anchor='w', fill='x')

        self.frame_options.grid_columnconfigure(0, weight=1)
        self.frame_options.grid_columnconfigure(1, weight=0)

        # Start/stop
        self.label_start = ctk.CTkLabel(self.frame_options, text=t('label_start'))
        self.label_start.grid(column=0, row=0, sticky='w', pady=[0,5])

        self.entry_start = TimeEntry(self.frame_options, width=100)
        self.entry_start.grid(column='1', row='0', sticky='e', pady=[0,5])
        self.entry_start.insert(0, '00:00:00')

        self.label_stop = ctk.CTkLabel(self.frame_options, text=t('label_stop'))
        self.label_stop.grid(column=0, row=1, sticky='w', pady=[5,10])

        self.entry_stop = TimeEntry(self.frame_options, width=100)
        self.entry_stop.grid(column='1', row='1', sticky='e', pady=[5,10])
    
        # language
        self.label_language = ctk.CTkLabel(self.frame_options, text=t('label_language'))
        self.label_language.grid(column=0, row=2, sticky='w', pady=5)

        self.langs = ('auto', 'en (english)', 'zh (chinese)', 'de (german)', 'es (spanish)', 'ru (russian)', 'ko (korean)', 'fr (french)', 'ja (japanese)', 'pt (portuguese)', 'tr (turkish)', 'pl (polish)', 'ca (catalan)', 'nl (dutch)', 'ar (arabic)', 'sv (swedish)', 'it (italian)', 'id (indonesian)', 'hi (hindi)', 'fi (finnish)', 'vi (vietnamese)', 'iw (hebrew)', 'uk (ukrainian)', 'el (greek)', 'ms (malay)', 'cs (czech)', 'ro (romanian)', 'da (danish)', 'hu (hungarian)', 'ta (tamil)', 'no (norwegian)', 'th (thai)', 'ur (urdu)', 'hr (croatian)', 'bg (bulgarian)', 'lt (lithuanian)', 'la (latin)', 'mi (maori)', 'ml (malayalam)', 'cy (welsh)', 'sk (slovak)', 'te (telugu)', 'fa (persian)', 'lv (latvian)', 'bn (bengali)', 'sr (serbian)', 'az (azerbaijani)', 'sl (slovenian)', 'kn (kannada)', 'et (estonian)', 'mk (macedonian)', 'br (breton)', 'eu (basque)', 'is (icelandic)', 'hy (armenian)', 'ne (nepali)', 'mn (mongolian)', 'bs (bosnian)', 'kk (kazakh)', 'sq (albanian)', 'sw (swahili)', 'gl (galician)', 'mr (marathi)', 'pa (punjabi)', 'si (sinhala)', 'km (khmer)', 'sn (shona)', 'yo (yoruba)', 'so (somali)', 'af (afrikaans)', 'oc (occitan)', 'ka (georgian)', 'be (belarusian)', 'tg (tajik)', 'sd (sindhi)', 'gu (gujarati)', 'am (amharic)', 'yi (yiddish)', 'lo (lao)', 'uz (uzbek)', 'fo (faroese)', 'ht (haitian   creole)', 'ps (pashto)', 'tk (turkmen)', 'nn (nynorsk)', 'mt (maltese)', 'sa (sanskrit)', 'lb (luxembourgish)', 'my (myanmar)', 'bo (tibetan)', 'tl (tagalog)', 'mg (malagasy)', 'as (assamese)', 'tt (tatar)', 'haw (hawaiian)', 'ln (lingala)', 'ha (hausa)', 'ba (bashkir)', 'jw (javanese)', 'su (sundanese)')

        self.option_menu_language = ctk.CTkOptionMenu(self.frame_options, width=100, values=self.langs)
        self.option_menu_language.grid(column=1, row=2, sticky='e', pady=5)
        try:
            self.option_menu_language.set(config['last_language'])
        except:
            pass

        # Quality (Model Selection)
        self.label_quality = ctk.CTkLabel(self.frame_options, text=t('label_quality'))
        self.label_quality.grid(column=0, row=3, sticky='w', pady=5)
        
        self.option_menu_quality = ctk.CTkOptionMenu(self.frame_options, width=100, values=['precise', 'fast'])
        self.option_menu_quality.grid(column=1, row=3, sticky='e', pady=5)
        try:
            self.option_menu_quality.set(config['last_quality'])
        except:
            pass

        # Speaker Detection (Diarization)
        self.label_speaker = ctk.CTkLabel(self.frame_options, text=t('label_speaker'))
        self.label_speaker.grid(column=0, row=4, sticky='w', pady=5)

        self.option_menu_speaker = ctk.CTkOptionMenu(self.frame_options, width=100, values=['auto', 'none'])
        self.option_menu_speaker.grid(column=1, row=4, sticky='e', pady=5)
        try:
            self.option_menu_speaker.set(config['last_speaker'])
        except:
            pass

        # Parallel Speaking (Diarization)
        self.label_parallel = ctk.CTkLabel(self.frame_options, text=t('label_parallel'))
        self.label_parallel.grid(column=0, row=5, sticky='w', pady=5)

        self.check_box_parallel = ctk.CTkCheckBox(self.frame_options, text = '')
        self.check_box_parallel.grid(column=1, row=5, sticky='e', pady=5)
        try:
            if config['last_parallel']:
                self.check_box_parallel.select()
            else:
                self.check_box_parallel.deselect()
        except:
            self.check_box_parallel.select() # default to on

        # Start Button
        self.start_button = ctk.CTkButton(self.sidebar_frame, height=42, text=t('start_button'), command=self.button_start_event)
        self.start_button.pack(padx=20, pady=[0,10], expand=True, fill='x', anchor='sw')

        # Stop Button
        self.stop_button = ctk.CTkButton(self.sidebar_frame, height=42, fg_color='darkred', hover_color='darkred', text=t('stop_button'), command=self.button_stop_event)
    
        # create log textbox
        self.log_frame = ctk.CTkFrame(self.frame_main, corner_radius=0, fg_color='transparent')
        self.log_frame.pack(padx=0, pady=0, fill='both', expand=True, side='right')

        self.log_textbox = ctk.CTkTextbox(self.log_frame, wrap='word', state="disabled", font=("",16), text_color="lightgray")
        self.log_textbox.tag_config('highlight', foreground='darkorange')
        self.log_textbox.tag_config('error', foreground='yellow')
        self.log_textbox.pack(padx=20, pady=[20,10], expand=True, fill='both')

        self.hyperlink = HyperlinkManager(self.log_textbox._textbox)
        
        # status bar bottom
        self.frame_status = ctk.CTkFrame(self, height=20, corner_radius=0)
        self.frame_status.pack(padx=0, pady=[0,0], anchor='sw', fill='x', side='bottom')

        self.progress_bar = ctk.CTkProgressBar(self.frame_status, height=5, mode='determinate')
        self.progress_bar.set(0)
        
        self.logn(t('welcome_message'), 'highlight')
        self.log(t('welcome_credits', v=app_version))
        self.logn('https://github.com/kaixxx/noScribe', link='https://github.com/kaixxx/noScribe#readme')
        self.logn(t('welcome_instructions'))       
        
    # Events and Methods
    
    def openLink(self, link):
        webbrowser.open(link)

    def log(self, txt='', tags=[], where='both', link=''): # log to main window (log can be 'screen', 'file' or 'both')
        if where != 'file':
            self.log_textbox.configure(state=ctk.NORMAL)
            if link != '':
                tags = tags + self.hyperlink.add(partial(self.openLink, link))
            self.log_textbox.insert(ctk.END, txt, tags)
            self.log_textbox.yview_moveto(1) # scroll to last line
            self.log_textbox.configure(state=ctk.DISABLED)
        if (where != 'screen') and (self.log_file != None) and (self.log_file.closed == False):
            if tags == 'error':
                txt = f'ERROR: {txt}'
            self.log_file.write(txt)
    
    def logn(self, txt='', tags=[], where='both', link=''): # log with newline
        self.log(f'{txt}\n', tags, where, link)

    def logr(self, txt='', tags=[], where='both', link=''): # replace the last line of the log
        if where != 'file':
            self.log_textbox.configure(state=ctk.NORMAL)
            self.log_textbox.delete('end-2l linestart', 'end-1l')
        self.logn(txt, tags, where, link)
        
    def reader_thread(self, q):
        try:
            with self.process.stdout as pipe:
                for line in iter(pipe.readline, b''):
                    q.put(line)
        finally:
            q.put(None)

    def button_audio_file_event(self):
        fn = tk.filedialog.askopenfilename()
        if fn != '':
            self.audio_file = fn
            self.logn(t('log_audio_file_selected') + self.audio_file)
            self.label_audio_file_name.configure(text=os.path.basename(self.audio_file))

    def button_transcript_file_event(self):
        fn = tk.filedialog.asksaveasfilename(filetypes=[('noScribe Transcript','*.html')], defaultextension='html')
        if fn != '':
            self.transcript_file = fn
            self.logn(t('log_transcript_filename') + self.transcript_file)
            self.label_transcript_file_name.configure(text=os.path.basename(self.transcript_file))
    
    def set_progress(self, step, value):
        if step == 1:
            self.progress_bar.set(value * 0.05 / 100)
        elif step == 2:
            progr = 0.05 # (step 1)
            progr = progr + (value * 0.45 / 100)
            self.progress_bar.set(progr)
        elif step == 3:
            if self.speaker_detection == 'auto':
                progr = 0.05 + 0.45 # (step 1 + step 2)
                progr_factor = 0.5
            else:
                progr = 0.05 # (step 1)
                progr_factor = 0.95
            progr = progr + (value * progr_factor / 100)
            self.progress_bar.set(progr)
        else:
            self.progress_bar.set(0)
        self.update()


    ################################################################################################
    # main function Button Start

    def button_start_event(self):
        
        proc_start_time = datetime.datetime.now()
        self.cancel = False

        # Show the stop button
        self.start_button.pack_forget() # hide
        self.stop_button.pack(padx=20, pady=[0,10], expand=True, fill='x', anchor='sw')
        
        # Show the progress bar
        self.progress_bar.set(0)
        self.progress_bar.pack(padx=20, pady=[10,20], expand=True, fill='both')

        tmpdir = TemporaryDirectory('noScribe')
        self.tmp_audio_file = tmpdir.name + '/' + 'tmp_audio.wav'
     
        try:
            # collect all the options
            if self.audio_file == '':
                self.logn(t('err_no_audio_file'), 'error')
                tk.messagebox.showerror(title='noScribe', message=t('err_no_audio_file'))
                return
            
            if self.transcript_file == '':
                self.logn(t('err_no_transcript_file'), 'error')
                tk.messagebox.showerror(title='noScribe', message=t('err_no_transcript_file'))
                return

            self.my_transcript_file = self.transcript_file

            val = self.entry_start.get()
            if val == '':
                self.start = 0
            else:
                self.start = millisec(val)
            
            val = self.entry_stop.get()
            if val == '':
                self.stop = '0'
            else:
                self.stop = millisec(val)
            
            if self.option_menu_quality.get() == 'fast':
                self.whisper_model = os.path.join(app_dir, 'models', 'faster-whisper-small')
                """
                try:
                    self.whisper_model = config['model_path_fast']
                except:
                    config['model_path_fast'] = os.path.join(app_dir, 'models', 'faster-whisper-small')
                    self.whisper_model = config['model_path_fast']
                """
            else:
                self.whisper_model = os.path.join(app_dir, 'models', 'faster-whisper-large-v2')
                """
                try:
                    self.whisper_model = config['model_path_precise']
                except:
                    config['model_path_precise'] = os.path.join(app_dir, 'models', 'faster-whisper-large-v2')
                    self.whisper_model = config['model_path_precise']
                """

            self.prompt = ''
            try:
                with open(os.path.join(app_dir, 'prompt.yml'), 'r', encoding='utf-8') as file:
                    prompts = yaml.safe_load(file)
            except:
                prompts = {}

            self.language = self.option_menu_language.get()
            if self.language != 'auto':
                self.language = self.language[0:3].strip()
                try:
                    self.prompt = prompts[self.language]
                except:
                    self.prompt = ''
            
            self.speaker_detection = self.option_menu_speaker.get()
            
            self.parallel = self.check_box_parallel.get()

            try:
                if config['auto_save'] == 'True': # auto save during transcription (every 20 sec)?
                    self.auto_save = True
                else:
                    self.auto_save = False
            except:
                config['auto_save'] = 'True'
                self.auto_save = True 

            if platform.system() == "Darwin": # = MAC
                if platform.mac_ver()[0] >= '12.3':
                    try:
                        if config['macos_xpu'] == 'cpu':
                            self.macos_xpu = 'cpu'
                        else:
                            self.macos_xpu = 'mps'
                    except:
                        config['macos_xpu'] = 'mps'
                        self.macos_xpu = 'mps'
                else:
                    try:
                        if config['macos_xpu'] == 'cpu':
                            self.macos_xpu = 'cpu'
                        else:
                            self.macos_xpu = 'cpu'
                    except:
                        config['macos_xpu'] = 'cpu'
                        self.macos_xpu = 'cpu'

            # create log file
            if not os.path.exists(f'{config_dir}/log'):
                os.makedirs(f'{config_dir}/log')
            self.log_file = open(f'{config_dir}/log/{Path(self.audio_file).stem}.log', 'w', encoding="utf-8")

            # log CPU capabilities
            self.logn("=== CPU FEATURES ===", where="file")
            if platform.system() == 'Windows':
                self.logn("System: Windows", where="file")
                for key, value in cpufeature.CPUFeature.items():
                    self.logn('    {:24}: {}'.format(key, value), where="file")
            elif platform.system() == "Darwin": # = MAC
                if platform.machine() == "arm64":
                    self.logn("System: MAC arm64", where="file")
                elif platform.machine() == "x86_64":
                    self.logn("System: MAC x86_64", where="file")
                if platform.mac_ver()[0] >= '12.3': # MPS needs macOS 12.3+
                    if config['macos_xpu'] == 'mps':
                        self.logn("macOS version >= 12.3:\nUsing MPS (with PYTORCH_ENABLE_MPS_FALLBACK enabled)")
                    elif config['macos_xpu'] == 'cpu':
                        self.logn("macOS version >= 12.3:\nUser selected to use CPU (results will be better, but you might wanna make yourself a coffee)")
                    else:
                        self.logn("macOS version >= 12.3:\nInvalid option for 'macos_xpu' in config.yaml (should be 'mps' or 'cpu')\nYou might wanna change this\nUsing MPS anyway (with PYTORCH_ENABLE_MPS_FALLBACK enabled)")
                else:
                    self.logn("macOS version < 12.3:\nMPS not available: Using CPU\nPerformance might be poor\nConsider updating macOS, if possible")
            
            try:

                #-------------------------------------------------------
                # 1) Convert Audio

                try:
                    self.logn()
                    self.logn(t('start_audio_conversion'), 'highlight')
                    self.update()
                
                    if int(self.stop) > 0: # transcribe only part of the audio
                        end_pos_cmd = f'-to {self.stop}ms'
                    else: # tranbscribe until the end
                        end_pos_cmd = ''

                    if platform.system() == 'Windows':
                        ffmpeg_cmd = f'ffmpeg.exe -loglevel warning -y -ss {self.start}ms {end_pos_cmd} -i \"{self.audio_file}\" -ar 16000 -ac 1 -c:a pcm_s16le {self.tmp_audio_file}'
                    elif platform.system() == "Darwin":  # = MAC
                        ffmpeg_abspath = os.path.join(app_dir, 'ffmpeg')
                        ffmpeg_cmd = f'{ffmpeg_abspath} -nostdin -loglevel warning -y -ss {self.start}ms {end_pos_cmd} -i \"{self.audio_file}\" -ar 16000 -ac 1 -c:a pcm_s16le {self.tmp_audio_file}'
                        ffmpeg_cmd = shlex.split(ffmpeg_cmd)
                    else:
                        raise Exception('Platform not supported yet.')
                    self.logn(ffmpeg_cmd, where='file')

                    if platform.system() == 'Windows':
                        # (supresses the terminal, see: https://stackoverflow.com/questions/1813872/running-a-process-in-pythonw-with-popen-without-a-console)
                        startupinfo = STARTUPINFO()
                        startupinfo.dwFlags |= STARTF_USESHOWWINDOW
                        with Popen(ffmpeg_cmd, stdout=PIPE, stderr=STDOUT, bufsize=1,universal_newlines=True,encoding='utf-8', startupinfo=startupinfo) as ffmpeg_proc:
                            for line in ffmpeg_proc.stdout:
                                self.logn('ffmpeg: ' + line)
                    elif platform.system() == "Darwin":  # = MAC
                        with Popen(ffmpeg_cmd, stdout=PIPE, stderr=STDOUT, bufsize=1,universal_newlines=True,encoding='utf-8') as ffmpeg_proc:
                            for line in ffmpeg_proc.stdout:
                                self.logn('ffmpeg: ' + line)
                    if ffmpeg_proc.returncode > 0:
                        raise Exception(t('err_ffmpeg'))
                    self.logn(t('audio_conversion_finished'))
                    self.set_progress(1, 50)
                except Exception as e:
                    self.logn(t('err_converting_audio'), 'error')
                    self.logn(e, 'error')
                    return

                #-------------------------------------------------------
                # 2) Speaker identification (diarization) with pyannote
                
                # Helper Functions:

                def overlap_len(ss_start, ss_end, ts_start, ts_end):
                    # ss...: speaker segment start and end in milliseconds (from pyannote)
                    # ts...: transcript segment start and end (from whisper.cpp)
                    # returns overlap percentage, i.e., "0.8" = 80% of the transcript segment overlaps with the speaker segment from pyannote  
                    if ts_end < ss_start: # no overlap, ts is before ss
                        return -1   
                    elif ts_start > ss_end: # no overlap, ts is after ss
                        return 0
                    else: # ss & ts have overlap
                        if ts_start > ss_start: # ts starts after ss
                            overlap_start = ts_start
                        else:
                            overlap_start = ss_start
                        if ts_end > ss_end: # ts ends after ss
                            overlap_end = ss_end
                        else:
                            overlap_end = ts_end
                        ol_len = overlap_end - overlap_start + 1
                        ts_len = ts_end - ts_start
                        if ts_len == 0:
                            return -1
                        else:
                            return ol_len / ts_len

                def find_speaker(diarization, transcript_start, transcript_end):
                    # Looks for the shortest segment in diarization that has at least 80% overlap 
                    # with transcript_start - trancript_end.  
                    # Returns the speaker name if found.
                    # If only an overlap < 80% is found, this speaker name ist returned.
                    # If no overlap is found, an empty string is returned.
                    spkr = ''
                    overlap_found = 0
                    overlap_threshold = 0.8
                    segment_len = 0
                    is_parallel = False
                    
                    for segment, _, label in diarization.itertracks(yield_label=True):
                        t = overlap_len(int(segment.start * 1000), int((segment.start + segment.duration) * 1000), transcript_start, transcript_end)
                        if t == -1: # we are already after transcript_end
                            break
                        else:
                            if overlap_found >= overlap_threshold: # we already found a fitting segment, compare length now
                                if (t >= overlap_threshold) and (segment.duration * 1000 < segment_len): # found a shorter (= better fitting) segment that also overlaps well
                                    is_parallel = True
                                    overlap_found = t
                                    segment_len = segment.duration * 1000
                                    spkr = f'S{label[8:]}' # shorten the label: "SPEAKER_01" > "S01"
                            elif t > overlap_found: # no segment with good overlap jet, take this if the overlap is better then previously found 
                                overlap_found = t
                                segment_len = segment.duration * 1000
                                spkr = f'S{label[8:]}' # shorten the label: "SPEAKER_01" > "S01"

                    if self.parallel and is_parallel:
                        return f"//{spkr}"
                    else:
                        return spkr

                class SimpleProgressHook:
                    #Hook to show progress of each internal step
                    def __init__(self, parent, transient: bool = False):
                        super().__init__()
                        self.parent = parent
                        self.transient = transient

                    def __enter__(self):
                        self.progress = 0
                        return self

                    def __exit__(self, *args):
                        pass
                        # self.parent.logn() # print the final new line

                    def __call__(
                        self,
                        step_name: Text,
                        step_artifact: Any,
                        file: Optional[Mapping] = None,
                        total: Optional[int] = None,
                        completed: Optional[int] = None,
                    ):        
                        # check for unser cancelation
                        if self.parent.cancel == True:
                            raise Exception(t('err_user_cancelation')) 
                        
                        if completed is None:
                            completed = total = 1

                        if not hasattr(self, 'step_name') or step_name != self.step_name:
                            self.step_name = step_name
                        
                        progress_percent = int(completed/total*100)
                        self.parent.logr(f'{step_name}: {progress_percent}%')
                        
                        if self.step_name == 'segmentation':
                            self.parent.set_progress(2, progress_percent * 0.3)
                        elif self.step_name == 'embeddings':
                            self.parent.set_progress(2, 30 + (progress_percent * 0.7))
                        
                        self.parent.update()

                # Start Diarization:

                if self.speaker_detection == 'auto':
                    try: 
                        with redirect_stderr(StringIO()) as f:
                            self.logn()
                            self.logn(t('start_identifiying_speakers'), 'highlight')
                            self.logn(t('loading_pyannote'))
                            self.update()
                            from pyannote.audio import Pipeline # import only on demand because this library is huge
                            self.set_progress(1, 100)

                            if platform.system() == 'Windows':
                                pipeline = Pipeline.from_pretrained(os.path.join(app_dir, 'models', 'pyannote_config.yaml'))
                            elif platform.system() == "Darwin": # = MAC
                                with open(os.path.join(app_dir, 'models', 'pyannote_config.yaml'), 'r') as yaml_file:
                                    pyannote_config = yaml.safe_load(yaml_file)

                                pyannote_config['pipeline']['params']['embedding'] = os.path.join(app_dir, *pyannote_config['pipeline']['params']['embedding'].split("/")[1:])
                                pyannote_config['pipeline']['params']['segmentation'] = os.path.join(app_dir, *pyannote_config['pipeline']['params']['segmentation'].split("/")[1:])

                                with open(os.path.join(app_dir, 'models', 'pyannote_config_macOS.yaml'), 'w') as yaml_file:
                                    yaml.safe_dump(pyannote_config, yaml_file)

                                pipeline = Pipeline.from_pretrained(os.path.join(app_dir, 'models', 'pyannote_config_macOS.yaml'))
                                # if platform.machine() == "arm64": # Intel should also support MPS
                                if platform.mac_ver()[0] >= '12.3': # MPS needs macOS 12.3+
                                    pipeline.to(self.macos_xpu)
                            else:
                                raise Exception('Platform not supported yet.')
                            self.logn()
                            with SimpleProgressHook(parent=self) as hook:
                                diarization = pipeline(self.tmp_audio_file, hook=hook) # apply the pipeline to the audio file

                            # write segments to log file 
                            for segment, _, label in diarization.itertracks(yield_label=True):
                                line = (
                                    f'{int(segment.start * 1000)} {int((segment.start + segment.duration) * 1000)} {label}\n'
                                )
                                self.log(line, where='file')
                                
                            self.logn()
                            
                            # read stderr and log it:
                            err = f.readline()
                            while err != '':
                                self.logn(err, 'error')
                                err = f.readline()
                    except Exception as e:
                        self.logn(t('err_identifying_speakers'), 'error')
                        self.logn(e, 'error')
                        return

                #-------------------------------------------------------
                # 3) Transcribe with faster-whisper

                self.logn()
                self.logn(t('start_transcription'), 'highlight')
                self.logn(t('loading_whisper'))
                self.logn()
                self.update()
                               
                # whisper options:
                """
                try:
                    # max segement length. Shorter segments can improve speaker identification.
                    self.whisper_options = f"--max-len {config['whisper_options_max-len']}" 
                except:
                    config['whisper_options_max-len'] = '30'
                    self.whisper_options = "--max-len 30"
                
                # "whisper_extra_commands" can be defined in config.yml and will be attached to the end of the command line. 
                # Use this to experiment with advanced options.
                # see https://github.com/ggerganov/whisper.cpp/tree/master/examples/main for a list of options
                # Be careful: If your options change the output of main.exe in the terminal, noScribe might not be able to interpret this and fail badly...

                try:
                    self.whisper_extra_commands = config['whisper_extra_commands']
                    if self.whisper_extra_commands == None:
                        self.whisper_extra_commands = ''
                except:
                    config['whisper_extra_commands'] = ''
                    self.whisper_extra_commands = ''
                
                command = f'{whisper_path}/main --model {self.whisper_model} --language {self.language} {self.prompt_cmd} {self.whisper_options} --print-colors --print-progress --file "{self.tmp_audio_file}" {self.whisper_extra_commands}'
                if platform.system() == "Darwin":  # = MAC
                    command = shlex.split(command)
                self.logn(command, where='file')

                """

                # prepare transcript html
                d = AdvancedHTMLParser.AdvancedHTMLParser()
                d.parseStr(default_html)                
                
                # add audio file path:
                tag = d.createElement("meta")
                tag.name = "audio_source"
                tag.content = self.audio_file
                d.head.appendChild(tag)

                # add app version:
                """ # removed because not really necessary
                tag = d.createElement("meta")
                tag.name = "noScribe_version"
                tag.content = app_version
                d.head.appendChild(tag)
                """
                
                #add WordSection1 (for line numbers in MS Word) as main_body
                main_body = d.createElement('div')
                main_body.addClass('WordSection1')
                d.body.appendChild(main_body)
                
                # header               
                p = d.createElement('p')
                p.setStyle('font-weight', '600')
                p.appendText(Path(self.audio_file).stem) # use the name of the audio file (without extension) as the title
                main_body.appendChild(p)
                
                # subheader
                p = d.createElement('p')
                p.setStyle('color', '#909090')
                p.appendText(t('doc_header', version=app_version))
                br = d.createElement('br')
                p.appendChild(br)
                p.appendText(t('doc_header_audio', file=self.audio_file))
                main_body.appendChild(p)
                
                p = d.createElement('p')
                main_body.appendChild(p)
                speaker = ''
                bookmark_id = 0
                self.last_auto_save = datetime.datetime.now()

                def save_doc():
                    try:
                        htmlStr = d.asHTML()
                        with open(self.my_transcript_file, 'w', encoding="utf-8") as f:
                            f.write(htmlStr)
                        self.last_auto_save = datetime.datetime.now()
                    except:
                        # saving failed, maybe the file is already open in Word and cannot be overwritten
                        # try saving to a different filename
                        transcript_path = Path(self.my_transcript_file)
                        self.my_transcript_file = f'{transcript_path.parent}/{transcript_path.stem}_1.html'
                        if os.path.exists(self.my_transcript_file):
                            # the alternative filename also exists already, don't want to overwrite, giving up
                            raise Exception(t('rescue_saving_failed'))
                        else:
                            htmlStr = d.asHTML()
                            with open(self.my_transcript_file, 'w', encoding="utf-8") as f:
                                f.write(htmlStr)
                            self.logn()
                            self.logn(t('rescue_saving', file=self.my_transcript_file), 'error')
                            self.last_auto_save = datetime.datetime.now()
            
                try:
                    # model = WhisperModel(self.whisper_model, device="auto", compute_type="auto", local_files_only=True)
                    model = WhisperModel(self.whisper_model, device="cpu", cpu_threads=4, compute_type="int8", local_files_only=True)

                    if self.language != "auto":
                        whisper_lang = self.language
                    else:
                        whisper_lang = None
                    
                    # segments, info = model.transcribe(self.tmp_audio_file, language=whisper_lang, beam_size=5, word_timestamps=True, initial_prompt=self.prompt)
                    segments, info = model.transcribe(self.tmp_audio_file, language=whisper_lang, beam_size=1, temperature=0, word_timestamps=True, initial_prompt=self.prompt)

                    if self.language == "auto":
                        self.logn("Detected language '%s' with probability %f" % (info.language, info.language_probability))

                    for segment in segments:
                        self.update()
                        # check for user cancelation
                        if self.cancel == True:
                            if self.auto_save == True:
                                save_doc()
                                self.logn()
                                self.logn(t('transcription_saved', file=self.my_transcript_file))
                                raise Exception(t('err_user_cancelation')) 
                            else:    
                                raise Exception(t('err_user_cancelation')) 
                            
                        line = segment.text
                        
                        # get time of the segment in milliseconds
                        start = round(segment.start * 1000.0)
                        end = round(segment.end * 1000.0)
                                    
                        # write text to the doc
                        # diarization (speaker detection)?
                        if self.speaker_detection == 'auto':
                            spkr = find_speaker(diarization, start, end)
                            if (speaker != spkr) & (spkr != ''):
                                if spkr[:2] == '//': # is parallel speaking, create no new paragraph
                                    speaker = spkr
                                    line = f' {speaker}:{line}'                                
                                elif speaker[:2] == '//': # previous was parallel speaking, mark the end
                                    line = f'//{line}'
                                    speaker = spkr
                                else:
                                    speaker = spkr
                                    self.logn()
                                    p = d.createElement('p')
                                    main_body.appendChild(p)
                                    line = f'{speaker}:{line}'

                        # Mark confidence level (not implemented yet in html)
                        # cl_level = round((segment.avg_logprob + 1) * 10)
                        # TODO: better cl_level for words based on https://github.com/Softcatala/whisper-ctranslate2/blob/main/src/whisper_ctranslate2/transcribe.py
                        # if cl_level > 0:
                        #     r.style = d.styles[f'noScribe_cl{cl_level}']

                        # Create bookmark with audio timestamps start to end and add the current segment.
                        # This way, we can jump to the according audio position and play it later in the editor.
                        # if we skipped a part at the beginning of the audio we have to add this here again, otherwise the timestaps will not match the original audio:
                        orig_audio_start = self.start + start
                        orig_audio_end = self.start + end
                        a = d.createElement('a')
                        a.href = f'ts_{orig_audio_start}_{orig_audio_end}'
                        a.appendText(line)
                        p.appendChild(a)
                        
                        self.log(line)
                                    
                        # auto save
                        if self.auto_save == True:
                            if (datetime.datetime.now() - self.last_auto_save).total_seconds() > 20:
                                save_doc()    

                        self.update()
                        
                        progr = round((segment.end/info.duration) * 100)
                        self.set_progress(3, progr)

                    save_doc()
                    self.logn()
                    self.logn()
                    self.logn(t('transcription_finished'), 'highlight')
                    if self.transcript_file != self.my_transcript_file: # used alternative filename because saving under the initial name failed
                        self.logn(t('rescue_saving', file=self.my_transcript_file), 'error')
                    else:
                        self.log(t('transcription_saved'))
                        self.logn(self.my_transcript_file, link=f'file://{self.my_transcript_file}')
                    # log duration of the whole process in minutes
                    proc_time = datetime.datetime.now() - proc_start_time
                    self.logn(t('trancription_time', duration=int(proc_time.total_seconds() / 60))) 
                
                except Exception as e:
                    self.logn()
                    self.logn(t('err_transcription'), 'error')
                    self.logn(e, 'error')
                    return
                
            finally:
                self.log_file.close()
                self.log_file = None

        except Exception as e:
            self.logn(t('err_options'), 'error')
            self.logn(e, 'error')
            return
        
        finally:
            # hide the stop button
            self.stop_button.pack_forget() # hide
            self.start_button.pack(padx=20, pady=[0,10], expand=True, fill='x', anchor='sw')

            # hide progress bar
            self.progress_bar.pack_forget()

    # End main function Button Start        
    ################################################################################################

    def button_stop_event(self):
        if tk.messagebox.askyesno(title='noScribe', message=t('transcription_canceled')) == True:
            self.logn()
            self.logn(t('start_canceling'))
            self.update()
            self.cancel = True

    def on_closing(self):
        # (see: https://stackoverflow.com/questions/111155/how-do-i-handle-the-window-close-event-in-tkinter)
        #if messagebox.askokcancel("Quit", "Do you want to quit?"):
        try:
            # remember some settings for the next run
            config['last_language'] = self.option_menu_language.get()
            config['last_speaker'] = self.option_menu_speaker.get()
            config['last_quality'] = self.option_menu_quality.get()
            config['last_parallel'] = self.check_box_parallel.get()
            save_config()
        finally:
            self.destroy()

if __name__ == "__main__":

    app = App()
    
    app.mainloop()