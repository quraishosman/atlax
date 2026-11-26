const SIGNAL_HEADERS = [
  "record_key", "correlation_id", "created_at", "resolved_at", "symbol",
  "timeframe", "profile", "delivery_route", "direction", "classification",
  "confidence_score", "atr_status", "atr_grade", "parent_atr_ratio",
  "sweep_atr_ratio", "key_level_status", "key_level_zone",
  "key_level_match_count", "entry", "entry_low", "sl", "tp1", "tp2",
  "signal_status", "outcome", "exit", "pnl_r", "resolution_reason",
  "delivery_count", "notes"
];

const PAYMENT_HEADERS = [
  "reference", "chat_id", "username", "subscription_plan", "method",
  "amount_usd", "status", "submitted_tx_reference", "created_at",
  "submitted_at", "confirmed_at", "rejected_at", "expires_at",
  "confirmed_by", "rejected_by", "rejection_reason"
];

const SUBSCRIBER_HEADERS = [
  "chat_id", "username", "subscription_plan", "subscription_status", "style",
  "timeframes", "payment_method", "payment_reference", "subscription_source",
  "joined", "subscription_started_at", "subscription_expires_at", "updated_at"
];

const EVENT_HEADERS = ["event_id", "event_type", "correlation_id", "received_at"];
const SIGNAL_SHEETS = ["Signals All", "Standard Signals", "Premium Signals"];

function json_(value) {
  return ContentService.createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet() {
  return json_({ok: true, service: "ATLAX reporting", schema_version: "3.0"});
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    if (!e || !e.postData || !e.postData.contents) throw new Error("Missing request body");
    const payload = JSON.parse(e.postData.contents);
    validate_(payload);
    verifySignature_(payload);
    ensureSheets_();
    if (eventExists_(String(payload.event_id))) {
      return json_({ok: true, duplicate: true, event_id: payload.event_id});
    }
    routeEvent_(payload);
    appendEvent_(payload);
    return json_({ok: true, duplicate: false, event_id: payload.event_id});
  } catch (err) {
    return json_({ok: false, error: String(err && err.message || err)});
  } finally {
    lock.releaseLock();
  }
}

function validate_(payload) {
  if (!payload || payload.schema_version !== "3.0") throw new Error("Unsupported schema_version");
  if (!String(payload.event_type || "").trim()) throw new Error("Missing event_type");
  if (!String(payload.event_id || "").trim()) throw new Error("Missing event_id");
}

function verifySignature_(payload) {
  const secret = PropertiesService.getScriptProperties().getProperty("ATLAX_REPORTING_SHARED_SECRET");
  if (!secret) throw new Error("ATLAX_REPORTING_SHARED_SECRET is not configured");
  if (!payload.signature) throw new Error("Missing event signature");
  const material = [payload.event_type || "", payload.event_id || "",
    payload.correlation_id || "", payload.occurred_at || ""].join("|");
  const digest = Utilities.computeHmacSha256Signature(material, secret).map(function(byte) {
    const value = (byte < 0 ? byte + 256 : byte).toString(16);
    return value.length === 1 ? "0" + value : value;
  }).join("");
  if (digest !== String(payload.signature)) throw new Error("Invalid event signature");
}

function ensureSheets_() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  ensureSheet_(spreadsheet, "Signals All", SIGNAL_HEADERS, true);
  ensureSheet_(spreadsheet, "Standard Signals", SIGNAL_HEADERS, true);
  ensureSheet_(spreadsheet, "Premium Signals", SIGNAL_HEADERS, true);
  ensureSheet_(spreadsheet, "Payments", PAYMENT_HEADERS, false);
  ensureSheet_(spreadsheet, "Payment Events", ["event_id", "event_type"].concat(PAYMENT_HEADERS), false);
  ensureSheet_(spreadsheet, "Subscribers", SUBSCRIBER_HEADERS, false);
  ensureSheet_(spreadsheet, "Subscriber Events", ["event_id", "event_type"].concat(SUBSCRIBER_HEADERS), false);
  ensureSheet_(spreadsheet, "Grade Performance", ["route", "grade", "formed", "resolved", "wins", "losses", "win_rate", "average_r"], false);
  ensureSheet_(spreadsheet, "Event Log", EVENT_HEADERS, true);
}

function ensureSheet_(spreadsheet, name, headers, hideKey) {
  let sheet = spreadsheet.getSheetByName(name);
  if (!sheet) sheet = spreadsheet.insertSheet(name);
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  if (hideKey && !sheet.isColumnHiddenByUser(1)) sheet.hideColumns(1);
  return sheet;
}

function setupSheets() {
  ensureSheets_();
  formatReportingDates_();
  rebuildGradePerformance_();
}

function dateOnly_(value) {
  if (value === undefined || value === null || value === "") return "";
  const text = String(value);
  const parsed = new Date(text);
  if (!isNaN(parsed.getTime())) {
    return Utilities.formatDate(parsed, Session.getScriptTimeZone(), "yyyy-MM-dd");
  }
  return text.split("T")[0].split(" ")[0];
}

function formatReportingDates_() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  ["Signals All", "Standard Signals", "Premium Signals"].forEach(function(name) {
    const sheet = spreadsheet.getSheetByName(name);
    if (!sheet || sheet.getLastRow() < 2) return;
    const values = sheet.getRange(2, 3, sheet.getLastRow() - 1, 1).getValues();
    sheet.getRange(2, 3, values.length, 1).setValues(values.map(function(row) {
      return [dateOnly_(row[0])];
    }));
  });
  ["Payments", "Subscribers"].forEach(function(name) {
    const sheet = spreadsheet.getSheetByName(name);
    if (!sheet || sheet.getLastRow() < 2) return;
    const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
    headers.forEach(function(header, index) {
      if (["created_at", "joined", "subscription_started_at"].indexOf(String(header)) === -1) return;
      const values = sheet.getRange(2, index + 1, sheet.getLastRow() - 1, 1).getValues();
      sheet.getRange(2, index + 1, values.length, 1).setValues(values.map(function(row) {
        return [dateOnly_(row[0])];
      }));
    });
  });
}

function eventExists_(eventId) {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Event Log");
  if (!sheet || sheet.getLastRow() < 2) return false;
  return sheet.getRange(2, 1, sheet.getLastRow() - 1, 1).getValues().some(function(row) {
    return String(row[0]) === eventId;
  });
}

function appendEvent_(payload) {
  SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Event Log").appendRow([
    payload.event_id, payload.event_type, payload.correlation_id || "", new Date().toISOString()
  ]);
}

function routeEvent_(payload) {
  const eventType = String(payload.event_type).toLowerCase();
  if (eventType === "signal_formed") {
    upsertSignal_("Signals All", payload, String(payload.correlation_id) + "|all");
    rebuildGradePerformance_();
    return;
  }
  if (eventType === "signal_delivered") {
    const route = String(payload.delivery_route || "").toLowerCase();
    const sheetName = route === "premium" ? "Premium Signals" : "Standard Signals";
    upsertSignal_(sheetName, payload, String(payload.correlation_id) + "|" + route);
    rebuildGradePerformance_();
    return;
  }
  if (eventType === "signal_resolved") {
    resolveSignal_(payload);
    rebuildGradePerformance_();
    return;
  }
  if (eventType.indexOf("payment_") === 0) {
    upsertPayment_(payload);
    appendHistory_("Payment Events", PAYMENT_HEADERS, payload);
    return;
  }
  if (eventType.indexOf("subscriber_") === 0) {
    upsertSubscriber_(payload);
    appendHistory_("Subscriber Events", SUBSCRIBER_HEADERS, payload);
    return;
  }
  throw new Error("Unsupported event_type: " + eventType);
}

function signalRow_(payload, recordKey) {
  return [recordKey, payload.correlation_id || "", dateOnly_(payload.created_at || payload.occurred_at || ""),
    payload.resolved_at || "", payload.symbol || "", payload.timeframe || "", payload.profile || "",
    payload.delivery_route || "", payload.direction || "", payload.classification || "UNKNOWN",
    payload.confidence_score || "", payload.atr_status || "", payload.atr_grade || "",
    payload.parent_atr_ratio || "", payload.sweep_atr_ratio || "", payload.key_level_status || "",
    payload.key_level_zone === undefined ? "" : payload.key_level_zone,
    payload.key_level_match_count === undefined ? "" : payload.key_level_match_count,
    payload.entry || "", payload.entry_low || "", payload.sl || "", payload.tp1 || "", payload.tp2 || "",
    payload.signal_status || "OPEN", payload.outcome || "UNKNOWN", payload.exit || "", payload.pnl_r || "",
    payload.resolution_reason || "", payload.delivery_count || 0, payload.notes || ""];
}

function upsertSignal_(sheetName, payload, recordKey) {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(sheetName);
  const values = signalRow_(payload, recordKey);
  const row = findRow_(sheet, 1, recordKey);
  if (row) sheet.getRange(row, 1, 1, values.length).setValues([mergeRow_(sheet, row, values)]);
  else sheet.appendRow(values);
}

function resolveSignal_(payload) {
  SIGNAL_SHEETS.forEach(function(name) {
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
    rowsForCorrelation_(sheet, 2, payload.correlation_id).forEach(function(rowNumber) {
      const current = sheet.getRange(rowNumber, 1, 1, SIGNAL_HEADERS.length).getValues()[0];
      const updates = {4: payload.resolved_at || payload.occurred_at, 24: payload.signal_status || "RESOLVED",
        25: normalizeOutcome_(payload.outcome || payload.result), 26: payload.exit, 27: payload.pnl_r,
        28: payload.resolution_reason};
      Object.keys(updates).forEach(function(column) {
        const value = updates[column];
        if (value !== "" && value !== undefined && value !== null) current[Number(column) - 1] = value;
      });
      sheet.getRange(rowNumber, 1, 1, SIGNAL_HEADERS.length).setValues([current]);
    });
  });
}

function normalizeOutcome_(value) {
  const normalized = String(value || "UNKNOWN").toUpperCase();
  if (normalized.indexOf("WIN") >= 0) return "WIN";
  if (normalized.indexOf("LOSS") >= 0) return "LOSS";
  return "UNKNOWN";
}

function rebuildGradePerformance_() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const summary = spreadsheet.getSheetByName("Grade Performance");
  if (!summary) return;
  const routes = [["all", "Signals All"], ["standard", "Standard Signals"], ["premium", "Premium Signals"]];
  const grades = ["A+", "A", "B", "C", "UNKNOWN"];
  const output = [];
  routes.forEach(function(route) {
    const sheet = spreadsheet.getSheetByName(route[1]);
    const rows = sheet.getLastRow() < 2 ? [] : sheet.getRange(2, 1, sheet.getLastRow() - 1, SIGNAL_HEADERS.length).getValues();
    grades.forEach(function(grade) {
      const matching = rows.filter(function(row) { return String(row[12] || "UNKNOWN").toUpperCase() === grade; });
      const resolved = matching.filter(function(row) { return ["WIN", "LOSS"].indexOf(normalizeOutcome_(row[24])) >= 0; });
      const wins = resolved.filter(function(row) { return normalizeOutcome_(row[24]) === "WIN"; }).length;
      const losses = resolved.filter(function(row) { return normalizeOutcome_(row[24]) === "LOSS"; }).length;
      const rValues = resolved.map(function(row) { return Number(row[26]); }).filter(function(value) { return isFinite(value); });
      const averageR = rValues.length ? rValues.reduce(function(a, b) { return a + b; }, 0) / rValues.length : "";
      output.push([route[0], grade, matching.length, resolved.length, wins, losses,
        resolved.length ? wins / resolved.length : "", averageR]);
    });
  });
  if (summary.getLastRow() > 1) summary.getRange(2, 1, summary.getLastRow() - 1, 8).clearContent();
  if (output.length) summary.getRange(2, 1, output.length, 8).setValues(output);
}

function upsertPayment_(payload) {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Payments");
  const values = PAYMENT_HEADERS.map(function(header) {
    const value = payload[header] === undefined || payload[header] === null ? "" : payload[header];
    return header === "created_at" ? dateOnly_(value) : value;
  });
  const row = findRow_(sheet, 1, String(payload.reference || ""));
  if (row) sheet.getRange(row, 1, 1, values.length).setValues([mergeRow_(sheet, row, values)]);
  else sheet.appendRow(values);
}

function upsertSubscriber_(payload) {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Subscribers");
  const values = SUBSCRIBER_HEADERS.map(function(header) {
    const value = payload[header];
    if (["joined", "subscription_started_at"].indexOf(header) >= 0) return dateOnly_(value);
    return Array.isArray(value) ? value.join(", ") : (value === undefined || value === null ? "" : value);
  });
  const row = findRow_(sheet, 1, String(payload.chat_id || ""));
  if (row) sheet.getRange(row, 1, 1, values.length).setValues([mergeRow_(sheet, row, values)]);
  else sheet.appendRow(values);
}

function appendHistory_(sheetName, headers, payload) {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(sheetName);
  const values = [payload.event_id, payload.event_type].concat(headers.map(function(header) {
    const value = payload[header];
    if (["created_at", "joined", "subscription_started_at"].indexOf(header) >= 0) return dateOnly_(value);
    return Array.isArray(value) ? value.join(", ") : (value === undefined || value === null ? "" : value);
  }));
  sheet.appendRow(values);
}

function findRow_(sheet, column, value) {
  if (!sheet || sheet.getLastRow() < 2) return 0;
  const values = sheet.getRange(2, column, sheet.getLastRow() - 1, 1).getValues();
  for (let index = 0; index < values.length; index += 1) if (String(values[index][0]) === String(value)) return index + 2;
  return 0;
}

function rowsForCorrelation_(sheet, column, correlationId) {
  const rows = [];
  if (!sheet || sheet.getLastRow() < 2) return rows;
  sheet.getRange(2, column, sheet.getLastRow() - 1, 1).getValues().forEach(function(value, index) {
    if (String(value[0]) === String(correlationId)) rows.push(index + 2);
  });
  return rows;
}

function mergeRow_(sheet, rowNumber, incoming) {
  const current = sheet.getRange(rowNumber, 1, 1, incoming.length).getValues()[0];
  return incoming.map(function(value, index) {
    return value === "" || value === undefined || value === null ? current[index] : value;
  });
}
