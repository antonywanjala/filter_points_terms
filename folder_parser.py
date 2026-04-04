import os
import time
import sys
import multiprocessing
import queue
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sample import SamplesManager

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
LOCAL_VAR_FILE = "local_vars.txt"

# Store active processes: {slot_id: (process_handle, variable_dict)}
active_subprocesses = {}

def run_prelim(slot_id, log_queue, **kwargs):
    def log(msg):
        log_queue.put((slot_id, msg))
    try:
        log(f'{slot_id=}')
        manager = SamplesManager(slot_id, log_queue, **kwargs)
        manager.run(slot_id, log_queue)


    except Exception as e:
        log_queue.put((slot_id, f"Process Crashed: {str(e)}"))

def logger_worker(log_queue, log_mode):
    while True:
        try:
            record = log_queue.get(timeout=1)
            if record == "STOP":
                break

            # Verify we have a (slot, text) pair
            if isinstance(record, (tuple, list)) and len(record) == 2:
                slot, text = record
                output = f"Subprocess {slot}: {text}"
                print(output)

                # Write to files based on log_mode
                if str(log_mode) in ['2', '3']:
                    with open("master_log.txt", "a", encoding="utf-8") as f:
                        f.write(output + "\n")
                if str(log_mode) in ['1', '3']:
                    with open(f"subprocess_{slot}.txt", "a", encoding="utf-8") as f:
                        f.write(text + "\n")
            else:
                # This handles the '1' and '3' gracefully without crashing or spamming
                pass

        except queue.Empty:
            continue


def get_drive_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)


def load_local_variables(filepath):
    local_vars = {}
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if ":" in line and not line.startswith("#"):
                    key, val = line.split(":", 1)
                    local_vars[key.strip()] = val.strip()
    return local_vars

def parse_syntax(content, local_vars, log_queue):
    # Split content into blocks based on the Subprocess marker
    blocks = content.split('[Subprocess')

    for block in blocks:
        if not block.strip():
            continue

        # --- SURGICAL NAME EXTRACTION (NO RE) ---
        try:
            # block looks like " 1: FUNCTIONAL]..."
            # 1. Split at the first colon
            header_segment = block.split(':')[0].strip()
            # 2. Grab the first element and strip leading/trailing spaces to get "1"
            slot_id = header_segment.strip()

            # Safety: if the strip result is empty, call it Unknown
            if not slot_id:
                slot_id = "Unknown"
        except Exception:
            slot_id = "Unknown"

        full_block = '[Subprocess' + block

        # 1. Skip NOT FUNCTIONAL
        if "NOT FUNCTIONAL" in full_block:
            if slot_id in active_subprocesses:
                proc, _ = active_subprocesses[slot_id]
                if proc.is_alive():
                    # Send a log message so you know it's shutting down
                    log_queue.put((slot_id, "Shutting down: Status changed to NOT FUNCTIONAL"))
                    proc.terminate()
                    proc.join()  # Clean up the resource
                del active_subprocesses[slot_id]
            continue

        if "FUNCTIONAL" in full_block:
            context_vars = local_vars.copy()
            lines = full_block.split('\n')
            run_triggered = False

            for line in lines:
                # MANDATORY REQUIREMENT: Maintain this specific line
                while "[" in line and "]" in line:
                    start = line.find("[")
                    end = line.find("]") + 1
                    line = line[:start] + line[end:]
                    line = line.lstrip('\ufeff ')

                line = line.strip()
                if not line or line.startswith("#") or line.startswith("[Subprocess"):
                    continue

                if ":" in line and not line.startswith("run"):
                    key, val = line.split(":", 1)
                    # Clean the key (handle labels)
                    clean_key = key.split(']')[-1].strip() if ']' in key else key.strip()
                    context_vars[clean_key] = val.strip()
                elif line.startswith("run:"):
                    run_triggered = True

            if run_triggered:
                # 2. Incumbent vs Nascent Logic
                should_start = False
                if slot_id in active_subprocesses:
                    proc, existing_vars = active_subprocesses[slot_id]

                    # If variables match, let the incumbent continue
                    if proc.is_alive() and existing_vars == context_vars:
                        continue
                    else:
                        # Variables changed or proc died; restart
                        if proc.is_alive():
                            proc.terminate()
                        should_start = True
                else:
                    should_start = True

                if should_start:
                    """
                    p = multiprocessing.Process(
                        target=sample,
                        args=(slot_id, log_queue),
                        kwargs=context_vars
                    )
                    """
                    p = multiprocessing.Process(
                        target=run_prelim,
                        args=(slot_id, log_queue),
                        kwargs=context_vars
                    )
                    p.start()
                    # Store by slot_id (1, 2, 3, etc.)
                    active_subprocesses[slot_id] = (p, context_vars)
def main(f_id=None, p_int=30, i_delay=30, log_choice=3):

    if f_id is not None:
        log_queue = multiprocessing.Queue()
        log_proc = multiprocessing.Process(target=logger_worker, args=(log_queue, log_choice))
        log_proc.start()

        service = get_drive_service()
        known_files = {}

        try:
            while True:
                local_vars = load_local_variables(LOCAL_VAR_FILE)
                query = f"'{f_id}' in parents"
                results = service.files().list(q=query, fields="files(id, name, modifiedTime, mimeType)").execute()
                items = results.get('files', [])

                for item in items:
                    f_id_item = item['id']
                    m_time = item['modifiedTime']

                    if f_id_item not in known_files or known_files[f_id_item] != m_time:
                        known_files[f_id_item] = m_time

                        if item['mimeType'] == 'application/vnd.google-apps.document':
                            request = service.files().export_media(fileId=f_id_item, mimeType='text/plain')
                        else:
                            request = service.files().get_media(fileId=f_id_item)

                        content = request.execute().decode('utf-8')
                        parse_syntax(content, local_vars, log_queue)

                        time.sleep(i_delay)

                time.sleep(p_int)
        except Exception as e:
            log_queue.put("STOP")
            log_proc.join()
            exc_type, exc_obj, exc_tb = sys.exc_info()
            fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            readout_one = str(exc_type)
            readout_two = str(fname)
            readout_three = str(exc_tb.tb_lineno)
            error_readout = readout_one + " " + readout_two + " " + readout_three
            print(error_readout)
            main(f_id=f_id, p_int=p_int, i_delay=i_delay, log_choice=log_choice)
    elif f_id is None:
        raise "f_id is None"

if __name__ == "__main__":
    multiprocessing.freeze_support()

    f_id = input("Enter the Google Drive Folder ID to be parsed: ")
    p_int = int(input("Enter the poll_interval (seconds): "))
    i_delay = int(input("Enter time between extralocal instantiations (seconds): "))
    print("\nLogging Options:\n1. Individual files\n2. Single master file\n3. Both")
    log_choice = int(input("Select choice (1/2/3): "))
    print(f'{f_id=}')
    print(f'{p_int=}')
    print(f'{i_delay=}')
    print(f'{log_choice=}')
    main(f_id=f_id, p_int=p_int, i_delay=i_delay, log_choice=log_choice)
