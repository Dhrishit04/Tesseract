// Plain-language names for the sidecar's skills, used by the activity log and the overview.

export type Requirement = "none" | "google" | "automation";

export interface CapabilityGroup {
  prefix: string;
  title: string;
  summary: string;
  requires: Requirement;
}

export const CAPABILITY_GROUPS: CapabilityGroup[] = [
  { prefix: "filesystem", title: "Files", summary: "Read, write, list and search files inside Tesseract's working folder.", requires: "none" },
  { prefix: "app_launcher", title: "Apps and windows", summary: "Open apps, files and links with their default program, and switch between open windows.", requires: "none" },
  { prefix: "gmail", title: "Gmail", summary: "Read and search your inbox, open threads, draft replies and send email.", requires: "google" },
  { prefix: "calendar", title: "Google Calendar", summary: "List, create, change and delete events, and check when you are free.", requires: "google" },
  { prefix: "web_automation", title: "Web browser", summary: "Open pages in a separate Chromium window, click, type, read content and take screenshots.", requires: "automation" },
  { prefix: "desktop_automation", title: "Desktop apps", summary: "Find windows, read their controls, click and type. Falls back to OCR when controls are not exposed.", requires: "automation" },
  { prefix: "office", title: "Office documents", summary: "Read and edit Word files, read workbooks, build pivot tables from CSV and create presentations.", requires: "automation" },
];

const TOOL_LABELS: Record<string, string> = {
  "filesystem.read_file": "Reading a file",
  "filesystem.write_file": "Writing a file",
  "filesystem.list_directory": "Listing a folder",
  "filesystem.search": "Searching files",
  "app_launcher.launch": "Opening an app",
  "app_launcher.list_windows": "Listing open windows",
  "app_launcher.focus_window": "Switching windows",
  "gmail.read_inbox": "Reading your inbox",
  "gmail.send_email": "Sending an email",
  "gmail.search": "Searching email",
  "gmail.draft_reply": "Drafting a reply",
  "gmail.get_thread": "Reading an email thread",
  "calendar.list_events": "Reading your calendar",
  "calendar.create_event": "Creating an event",
  "calendar.update_event": "Updating an event",
  "calendar.delete_event": "Deleting an event",
  "calendar.check_schedule": "Checking your availability",
  "web_automation.navigate": "Opening a web page",
  "web_automation.click": "Clicking on the page",
  "web_automation.type": "Typing on the page",
  "web_automation.extract": "Reading the page",
  "web_automation.get_page_content": "Reading the page",
  "web_automation.screenshot": "Capturing the page",
  "web_automation.wait_for": "Waiting for the page",
  "web_automation.close": "Closing the browser",
  "desktop_automation.find_window": "Finding a window",
  "desktop_automation.focus_window": "Switching windows",
  "desktop_automation.get_elements": "Reading window controls",
  "desktop_automation.click": "Clicking in an app",
  "desktop_automation.type": "Typing in an app",
  "desktop_automation.get_text": "Reading text from an app",
  "desktop_automation.screenshot": "Capturing the screen",
  "desktop_automation.ocr": "Reading the screen with OCR",
  "desktop_automation.resize_window": "Resizing a window",
  "desktop_automation.list_windows": "Listing open windows",
  "desktop_automation.close": "Ending desktop control",
  "office.read_document": "Reading a document",
  "office.edit_document": "Editing a document",
  "office.create_pivot_table": "Building a pivot table",
  "office.read_excel": "Reading a spreadsheet",
  "office.create_presentation": "Creating a presentation",
};

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name;
}
