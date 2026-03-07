import os
import glob
from PIL import Image, ImageCms

# 加上這行：解除超大圖檔的安全像素限制，消除 DecompressionBombWarning
Image.MAX_IMAGE_PIXELS = None

def make_poster(input_path, output_path="poster_output.pdf"):
    """
    將高畫質圖片轉換為符合印刷廠標準的立牌 PDF 檔
    - 目標實體尺寸：90 cm x 180 cm
    - 解析度：150 DPI
    - 寬度滿版，高度等比縮放置中，保留純白底色
    - 透過 ICC 描述檔進行專業 CMYK 轉換，保留最佳亮度
    """
    # 尺寸設定 (cm)
    cm_width = 90
    cm_height = 180
    dpi = 150
    
    # 將 cm 轉換為英吋 (1 inch = 2.54 cm) 再乘以 DPI 取得目標像素大小
    pixel_width = int(round((cm_width / 2.54) * dpi))
    pixel_height = int(round((cm_height / 2.54) * dpi))
    
    print(f"🖨️ 目標畫布尺寸: {pixel_width} x {pixel_height} pixels ({dpi} DPI)")
    
    try:
        # 1. 讀取圖片
        print(f"📄 讀取圖片: {input_path}")
        img = Image.open(input_path)
        
        # 2. 處理透明背景 (Alpha channel)
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            print("✨ 偵測到透明背景，正在加上純白底框...")
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3]) 
            img = background
        else:
            img = img.convert("RGB")
            
        # 3. 圖片縮放：寬度滿版，高度等比
        original_width, original_height = img.size
        ratio = pixel_width / original_width
        new_width = pixel_width
        new_height = int(round(original_height * ratio))
        
        print(f"🔍 圖片原始尺寸: {original_width} x {original_height}")
        print(f"📐 縮放後尺寸: {new_width} x {new_height}")
        
        # 進行最高品質的 Lanczos 縮放
        try:
            resample_filter = Image.Resampling.LANCZOS
        except AttributeError:
            resample_filter = Image.ANTIALIAS
            
        img_resized = img.resize((new_width, new_height), resample_filter)
        
        # 4. 使用 ICC 描述檔進行專業 CMYK 轉換
        print("🎨 準備進行專業 ICC 色彩轉換...")
        cmyk_profile_path = "JapanColor2011Coated.icc" 
        
        try:
            if not os.path.exists(cmyk_profile_path):
                raise FileNotFoundError(f"找不到描述檔 {cmyk_profile_path}")

            # 建立標準的 sRGB 與印刷廠的 CMYK 描述檔
            srgb_profile = ImageCms.createProfile("sRGB")
            cmyk_profile = ImageCms.getOpenProfile(cmyk_profile_path)
            
            # 轉換為 CMYK (修改為支援新版 Pillow 的 Intent 寫法)
            img_cmyk = ImageCms.profileToProfile(
                img_resized, 
                srgb_profile, 
                cmyk_profile, 
                renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,  # <--- 就是這裡改了
                outputMode="CMYK"
            )
            print("✨ 成功套用 ICC 描述檔，保留最佳亮度與色彩！")
            
        except Exception as e:
            print(f"⚠️ ICC 轉換失敗 ({e})，退回一般基礎轉換模式...")
            img_cmyk = img_resized.convert("CMYK")

        # 5. 建立最終的 CMYK 純白畫布
        canvas = Image.new("CMYK", (pixel_width, pixel_height), (0, 0, 0, 0))
        
        # 計算 Y 座標置中的偏移量
        x_offset = 0
        y_offset = (pixel_height - new_height) // 2
        
        print(f"📌 將圖片貼於座標: ({x_offset}, {y_offset})")
        canvas.paste(img_cmyk, (x_offset, y_offset))
        
        # 6. 輸出為 PDF (加入 quality=95 減少壓縮破壞，保留更高畫質)
        print(f"💾 正在儲存為 PDF...")
        canvas.save(output_path, "PDF", resolution=dpi, quality=95)
        print(f"✅ 成功匯出印刷檔: {output_path}")
        
    except FileNotFoundError:
        print(f"❌ 找不到圖片檔案: {input_path}，請確認圖片名稱或路徑是否正確。")
    except Exception as e:
        print(f"❌ 發生未預期的錯誤: {e}")

if __name__ == "__main__":
    # 尋找目前資料夾底下所有的 .png 檔案
    png_files = glob.glob("*.png")
    
    print("=========================================")
    print("🎨 Antigravity 立牌產生器 PRO 版 (批次處理模式) 🎨")
    print("=========================================")
    
    if not png_files:
        print("⚠️ 錯誤: 目前資料夾找不到任何 PNG 圖檔。")
    else:
        total_files = len(png_files)
        print(f"🔎 總共找到 {total_files} 個 PNG 圖檔，準備開始轉換...")
        print("-" * 40)
        
        for index, input_filename in enumerate(png_files, start=1):
            # 自動命名輸出檔：去附檔名後加上 _poster_ICC.pdf
            base_name = os.path.splitext(input_filename)[0]
            output_filename = f"{base_name}_poster_ICC.pdf"
            
            print(f"▶️ 正在處理 {index}/{total_files}: {input_filename}...")
            make_poster(input_filename, output_filename)
            print("-" * 40)
            
        print("🎉 批次處理完畢！")
