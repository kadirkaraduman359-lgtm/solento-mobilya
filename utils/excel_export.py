import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

H_FILL = PatternFill("solid", fgColor="1e293b")
H_FONT = Font(color="FFFFFF", bold=True)


def _header(ws, headers):
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = H_FILL
        cell.font = H_FONT
        cell.alignment = Alignment(horizontal="center")


def _autofit(ws):
    for col in ws.columns:
        ml = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(ml + 4, 45)


def export_sevk_ozet(sevkler, buf):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sevk Ozeti"
    _header(ws, ["ID", "Tarih", "Sehir", "Magaza", "Nakliye (TL)", "Iscilik (TL)", "Gider Toplam (TL)"])
    for s in sevkler:
        gider = sum(g.tutar for g in s.giderler)
        ws.append([s.id, s.tarih, s.magaza.sehir.ad, s.magaza.ad,
                   round(s.nakliye_ucreti, 2), round(s.iscilik, 2), round(gider, 2)])
    _autofit(ws)
    wb.save(buf)


def export_magaza_maliyet(magazalar, buf):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Magaza Maliyet"
    _header(ws, ["Sehir", "Magaza", "Sevk Sayisi", "Toplam Nakliye (TL)", "Toplam Iscilik (TL)", "Toplam Gider (TL)"])
    for m in magazalar:
        nakliye = sum(s.nakliye_ucreti for s in m.sevkler)
        iscilik = sum(s.iscilik for s in m.sevkler)
        gider = sum(g.tutar for s in m.sevkler for g in s.giderler)
        ws.append([m.sehir.ad, m.ad, len(m.sevkler),
                   round(nakliye, 2), round(iscilik, 2), round(gider, 2)])
    _autofit(ws)
    wb.save(buf)


def export_stok(ozet, buf):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Stok Durumu"
    _header(ws, ["Kod", "Urun Adi", "Birim", "Bakiye"])
    for item in ozet:
        ws.append([item["urun"].kod, item["urun"].ad, item["urun"].birim, round(item["bakiye"], 2)])
    _autofit(ws)
    wb.save(buf)


def export_ssh(bildirimleri, buf):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SSH Bildirimleri"
    _header(ws, ["ID", "Tarih", "Magaza", "Urun", "Paket", "Hasar Aciklamasi", "Talep Miktar", "Durum"])
    for b in bildirimleri:
        ws.append([b.id, b.tarih, b.magaza.ad, b.urun.ad,
                   b.paket.paket_adi if b.paket else "-",
                   b.hasar_aciklamasi, b.talep_miktar, b.durum])
    _autofit(ws)
    wb.save(buf)
