# Elon Bot Deployment Guide

Bu qo'llanma sizning Elon botingizni DigitalOcean droplet'ida systemd service sifatida ishga tushirish uchun.

## 🚀 Tezkor o'rnatish

1. **Fayllarni yuklang**:
   ```bash
   # Barcha fayllarni /root/elon papkasiga yuklang
   cd /root/elon
   ```

2. **Deploy scriptini ishga tushiring**:
   ```bash
   chmod +x deploy.sh
   ./deploy.sh
   ```

3. **Botni ishga tushiring**:
   ```bash
   sudo systemctl start elon-bot
   ```

## 📋 Boshqarish buyruqlari

### Bot holatini tekshirish
```bash
sudo systemctl status elon-bot
```

### Botni ishga tushirish
```bash
sudo systemctl start elon-bot
```

### Botni to'xtatish
```bash
sudo systemctl stop elon-bot
```

### Botni qayta ishga tushirish
```bash
sudo systemctl restart elon-bot
```

### Live loglarni ko'rish
```bash
sudo journalctl -u elon-bot -f
```

### So'nggi 100 ta log
```bash
sudo journalctl -u elon-bot -n 100
```

## 🔧 Sozlash

### Service faylini tahrirlash
```bash
sudo nano /etc/systemd/system/elon-bot.service
```

### Botni avtomatik ishga tushirish (boot da)
```bash
sudo systemctl enable elon-bot
```

### Botni avtomatik ishga tushirishni o'chirish
```bash
sudo systemctl disable elon-bot
```

## 📊 Monitoring

### Bot ishlayotganini tekshirish
```bash
ps aux | grep python3
```

### Memory ishlatishini ko'rish
```bash
sudo systemctl show elon-bot --property=MemoryCurrent
```

### CPU ishlatishini ko'rish
```bash
top -p $(pgrep -f "python3.*main.py")
```

## 🐛 Xatoliklarni tuzatish

### Service xatoliklarini ko'rish
```bash
sudo journalctl -u elon-bot --since "1 hour ago"
```

### Bot fayllarini tekshirish
```bash
ls -la /root/elon/
```

### Config faylini tekshirish
```bash
cat /root/elon/config.json
```

## 🔄 Yangilash

1. **Botni to'xtating**:
   ```bash
   sudo systemctl stop elon-bot
   ```

2. **Yangi kodlarni yuklang**

3. **Dependencies yangilang**:
   ```bash
   pip3 install -r req.txt
   ```

4. **Botni qayta ishga tushiring**:
   ```bash
   sudo systemctl start elon-bot
   ```

## 📁 Fayl tuzilishi

```
/root/elon/
├── main.py                 # Asosiy bot fayli
├── utils.py               # Utility funksiyalar
├── announcement_store.py  # Announcement storage
├── driver_store.py        # Driver storage
├── keyboards.py           # Bot keyboardlar
├── config.json           # Bot sozlamalari
├── elon-bot.service      # Systemd service fayli
├── start_bot.sh          # Startup script
├── deploy.sh             # Deployment script
├── req.txt              # Python dependencies
└── README_DEPLOYMENT.md  # Bu qo'llanma
```

## ⚡ Afzalliklari

- ✅ **Avtomatik qayta ishga tushish**: Bot xatolik yuz bersa avtomatik qayta ishga tushadi
- ✅ **Boot da ishga tushish**: Server qayta ishga tushganda bot avtomatik ishga tushadi
- ✅ **Log yozish**: Barcha loglar systemd journal'da saqlanadi
- ✅ **Resource limitlar**: Memory va CPU limitlari o'rnatilgan
- ✅ **Xavfsizlik**: Minimal privileges bilan ishlaydi
- ✅ **Monitoring**: Status va loglarni oson tekshirish mumkin

## 🆘 Yordam

Agar muammo yuz bersa:

1. **Loglarni tekshiring**: `sudo journalctl -u elon-bot -f`
2. **Service holatini tekshiring**: `sudo systemctl status elon-bot`
3. **Bot fayllarini tekshiring**: `ls -la /root/elon/`
4. **Config faylini tekshiring**: `cat /root/elon/config.json`
