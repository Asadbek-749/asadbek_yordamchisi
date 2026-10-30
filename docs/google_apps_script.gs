/**
 * Nargis -> Google Sheets (kalitsiz ulanish).
 * Jadvalda: Extensions (Kengaytmalar) -> Apps Script -> shu kodni qo'ying -> SECRET ni o'zgartiring
 * -> Deploy -> New deployment -> Web app (Execute as: Me, Who has access: Anyone).
 */
const SECRET = 'BU_YERGA_UZUN_MAXFIY_SOZ_YOZING';

function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    if (data.secret !== SECRET) return reply_({ ok: false, error: 'forbidden' });

    const ss = SpreadsheetApp.getActiveSpreadsheet();
    (data.sheets || []).forEach(function (s) {
      const sheet = ss.getSheetByName(s.title) || ss.insertSheet(s.title);
      sheet.clear();
      if (s.rows && s.rows.length) {
        const cols = Math.max.apply(null, s.rows.map(function (r) { return r.length; }));
        const rows = s.rows.map(function (r) {
          while (r.length < cols) r.push('');
          return r;
        });
        sheet.getRange(1, 1, rows.length, cols).setValues(rows);
        sheet.getRange(1, 1, 1, cols).setFontWeight('bold').setBackground('#DDEBF7');
        sheet.setFrozenRows(1);
      }
    });
    return reply_({ ok: true, url: ss.getUrl() });
  } catch (err) {
    return reply_({ ok: false, error: String(err) });
  }
}

function reply_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
