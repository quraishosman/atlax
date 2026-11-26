# Google Sheets Feedback Contract

Version: 1.0  
Status: LEGACY COMPATIBILITY  
Purpose: Mirror resolved ATLAX setup outcomes to Google Sheets for quality
research without replacing the Signal Passport or journal database.

The unified multi-tab reporting contract is now defined in
`docs/38_GOOGLE_SHEETS_REPORTING.md` and implemented by
`docs/GOOGLE_SHEETS_REPORTING.gs`. This document remains for the legacy
resolved-outcome payload and should not be used for the new deployment.

## Data Ownership

The ATLAX journal and Signal Passport remain the primary records. Google Sheets
is an analysis copy. A webhook failure must not change signal outcomes,
subscriber delivery, risk, or execution.

ATLAX sends the feedback row when the tracked setup resolves at stop loss or
TP2. Unresolved and expired setups remain available in the journal and are not
silently marked as losses in the sheet.

## Payload Columns

The Apps Script writes one row in this order:

```text
created_at, resolved_at, symbol, timeframe, profile, direction, confidence_score,
atr_status, atr_grade,
parent_atr_ratio, sweep_atr_ratio, key_level_status, key_level_zone,
key_level_match_count, nested_route, nested_contained, controller_event_id,
strategy_name, entry, exit, sl, tp, pips, result, signal_status,
htf_controller_timeframe, htf_bias, htf_relationship, htf_run_length,
htf_strong_run, notes
```

The quality fields are copied from the setup at alert time and the outcome
fields are added when the tracker resolves the setup. This preserves the
conditions that existed before the result was known.

Phase 2 H1 momentum evidence is compacted into the existing `notes` column,
including bias, relationship, run length, base score, proposed score, and
adjustment. It is not added as another sheet column.

## Apps Script

Create or replace the web app `doPost` with the following code. It writes new
feedback rows to a separate `SignalFeedback` tab so existing legacy rows in
`Sheet1` are not shifted or overwritten.

```javascript
const FEEDBACK_SHEET_NAME = "SignalFeedback";
const REMOVED_COLUMNS = [
  "schema_version", "signal_id", "date", "classification",
  "final_outcome", "exit_reason"
];
const HEADERS = [
  "created_at", "resolved_at", "symbol", "timeframe", "profile", "direction",
  "confidence_score", "atr_status", "atr_grade", "parent_atr_ratio",
  "sweep_atr_ratio", "key_level_status", "key_level_zone",
  "key_level_match_count", "nested_route", "nested_contained",
  "controller_event_id", "strategy_name", "entry", "exit", "sl", "tp",
  "pips", "result", "signal_status", "htf_controller_timeframe", "htf_bias",
  "htf_relationship", "htf_run_length", "htf_strong_run", "notes"
];

function migrateFeedbackColumns_() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(FEEDBACK_SHEET_NAME);
  if (!sheet) {
    sheet = SpreadsheetApp.getActiveSpreadsheet().insertSheet(FEEDBACK_SHEET_NAME);
  }
  if (sheet.getLastColumn() === 0) {
    sheet.appendRow(HEADERS);
    return sheet;
  }

  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  for (var index = headers.length - 1; index >= 0; index--) {
    if (REMOVED_COLUMNS.indexOf(String(headers[index])) !== -1) {
      sheet.deleteColumn(index + 1);
    }
  }

  if (sheet.getLastColumn() < HEADERS.length) {
    sheet.insertColumnsAfter(sheet.getLastColumn(), HEADERS.length - sheet.getLastColumn());
  }
  sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
  return sheet;
}

// Run this once manually after deploying the updated Apps Script.
function migrateFeedbackColumns() {
  migrateFeedbackColumns_();
}

function getFeedbackSheet_() {
  var spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = spreadsheet.getSheetByName(FEEDBACK_SHEET_NAME);
  if (!sheet) {
    sheet = spreadsheet.insertSheet(FEEDBACK_SHEET_NAME);
  }
  return migrateFeedbackColumns_();
}

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      throw new Error("Missing request body");
    }

    var payload = JSON.parse(e.postData.contents);
    var sheet = getFeedbackSheet_();
    var row = HEADERS.map(function (header) {
      return payload[header] === null || payload[header] === undefined
        ? ""
        : payload[header];
    });
    sheet.appendRow(row);

    return ContentService
      .createTextOutput(JSON.stringify({ ok: true, schema_version: "2.0" }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function doGet() {
  return ContentService
    .createTextOutput(JSON.stringify({ ok: true, service: "ATLAX feedback" }))
    .setMimeType(ContentService.MimeType.JSON);
}
```

Deploy the script as a Web App and run `migrateFeedbackColumns()` once from the
Apps Script editor. The next webhook request also applies the migration. Keep
the deployed URL in the ATLAX runtime environment. Do not commit the webhook
URL to source control. Rotate the old webhook after the new deployment is
verified.

## Feedback Review

Use the sheet to group resolved rows by `profile`, `timeframe`, `atr_grade`,
`sweep_atr_ratio`, `key_level_zone`, and `nested_route`. Compare outcomes only
after enough rows are resolved. The sheet must not be used to invent a new
threshold without an approved rulebook or ADR.
