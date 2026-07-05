param(
  [Parameter(Mandatory=$true)][string]$InputJson,
  [Parameter(Mandatory=$true)][string]$OutputJson
)

$ErrorActionPreference = "Stop"
$helperStopwatch = [System.Diagnostics.Stopwatch]::StartNew()

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null

function AwaitOperation($WinRtTask, $ResultType) {
  $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
      $_.Name -eq 'AsTask' -and
      $_.GetParameters().Count -eq 1 -and
      $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } |
    Select-Object -First 1).MakeGenericMethod($ResultType)
  $netTask = $asTaskGeneric.Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  return $netTask.Result
}

function BoxToHash($Rect) {
  return @{
    x = [string][decimal]$Rect.X
    y = [string][decimal]$Rect.Y
    width = [string][decimal]$Rect.Width
    height = [string][decimal]$Rect.Height
  }
}

function UnionBoxes($Boxes) {
  if ($null -eq $Boxes -or $Boxes.Count -eq 0) {
    return $null
  }
  $left = [double]::PositiveInfinity
  $top = [double]::PositiveInfinity
  $right = [double]::NegativeInfinity
  $bottom = [double]::NegativeInfinity
  foreach ($box in $Boxes) {
    if ($box.X -lt $left) { $left = $box.X }
    if ($box.Y -lt $top) { $top = $box.Y }
    if (($box.X + $box.Width) -gt $right) { $right = $box.X + $box.Width }
    if (($box.Y + $box.Height) -gt $bottom) { $bottom = $box.Y + $box.Height }
  }
  return @{
    x = [string][decimal]$left
    y = [string][decimal]$top
    width = [string][decimal]($right - $left)
    height = [string][decimal]($bottom - $top)
  }
}

function RecognizeImage($Path) {
  $totalStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $file = AwaitOperation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
  $stream = AwaitOperation ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
  $decoder = AwaitOperation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bitmap = AwaitOperation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $engineStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  $engineStopwatch.Stop()
  if ($null -eq $engine) {
    throw "Windows OCR engine is unavailable for the current user profile languages."
  }
  $ocrStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $result = AwaitOperation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  $ocrStopwatch.Stop()
  $lines = New-Object System.Collections.Generic.List[object]
  $lineOrder = 0
  foreach ($line in $result.Lines) {
    $words = New-Object System.Collections.Generic.List[object]
    $wordBoxes = New-Object System.Collections.Generic.List[object]
    $wordOrder = 0
    foreach ($word in $line.Words) {
      $wordBox = BoxToHash $word.BoundingRect
      $wordBoxes.Add($word.BoundingRect)
      $words.Add(@{
        text = [string]$word.Text
        order = $wordOrder
        bounding_box = $wordBox
      })
      $wordOrder += 1
    }
    $lines.Add(@{
      text = [string]$line.Text
      order = $lineOrder
      bounding_box = UnionBoxes -Boxes $wordBoxes.ToArray()
      words = @($words.ToArray())
    })
    $lineOrder += 1
  }
  $totalStopwatch.Stop()
  return @{
    text = [string]$result.Text
    lines = @($lines.ToArray())
    decoder = @{
      bitmap_pixel_format = [string]$decoder.BitmapPixelFormat
      bitmap_alpha_mode = [string]$decoder.BitmapAlphaMode
      dpi_x = [double]$decoder.DpiX
      dpi_y = [double]$decoder.DpiY
      pixel_width = [int]$decoder.PixelWidth
      pixel_height = [int]$decoder.PixelHeight
    }
    timing = @{
      total_ms = [int]$totalStopwatch.ElapsedMilliseconds
      engine_initialization_ms = [int]$engineStopwatch.ElapsedMilliseconds
      ocr_execution_ms = [int]$ocrStopwatch.ElapsedMilliseconds
    }
  }
}

function NewOcrEngine() {
  $engineStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  $engineStopwatch.Stop()
  if ($null -eq $engine) {
    throw "Windows OCR engine is unavailable for the current user profile languages."
  }
  return @{
    engine = $engine
    timing = [int]$engineStopwatch.ElapsedMilliseconds
  }
}

function RecognizePreparedImage($Path, $Engine) {
  $totalStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $imageOpenStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $file = AwaitOperation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
  $stream = AwaitOperation ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
  $imageOpenStopwatch.Stop()

  $bitmapDecodeStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $decoder = AwaitOperation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bitmap = AwaitOperation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $bitmapDecodeStopwatch.Stop()

  $ocrStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $result = AwaitOperation ($Engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  $ocrStopwatch.Stop()

  $serializationStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $lines = New-Object System.Collections.Generic.List[object]
  $lineOrder = 0
  foreach ($line in $result.Lines) {
    $words = New-Object System.Collections.Generic.List[object]
    $wordBoxes = New-Object System.Collections.Generic.List[object]
    $wordOrder = 0
    foreach ($word in $line.Words) {
      $wordBox = BoxToHash $word.BoundingRect
      $wordBoxes.Add($word.BoundingRect)
      $words.Add(@{
        text = [string]$word.Text
        order = $wordOrder
        bounding_box = $wordBox
      })
      $wordOrder += 1
    }
    $lines.Add(@{
      text = [string]$line.Text
      order = $lineOrder
      bounding_box = UnionBoxes -Boxes $wordBoxes.ToArray()
      words = @($words.ToArray())
    })
    $lineOrder += 1
  }
  $serializationStopwatch.Stop()

  $disposeStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  if ($null -ne $bitmap -and $bitmap -is [System.IDisposable]) { $bitmap.Dispose() }
  if ($null -ne $stream -and $stream -is [System.IDisposable]) { $stream.Dispose() }
  $disposeStopwatch.Stop()
  $totalStopwatch.Stop()

  return @{
    text = [string]$result.Text
    lines = @($lines.ToArray())
    decoder = @{
      bitmap_pixel_format = [string]$decoder.BitmapPixelFormat
      bitmap_alpha_mode = [string]$decoder.BitmapAlphaMode
      dpi_x = [double]$decoder.DpiX
      dpi_y = [double]$decoder.DpiY
      pixel_width = [int]$decoder.PixelWidth
      pixel_height = [int]$decoder.PixelHeight
    }
    timing = @{
      total_ms = [int]$totalStopwatch.ElapsedMilliseconds
      image_open_ms = [int]$imageOpenStopwatch.ElapsedMilliseconds
      bitmap_decode_ms = [int]$bitmapDecodeStopwatch.ElapsedMilliseconds
      recognize_ms = [int]$ocrStopwatch.ElapsedMilliseconds
      serialization_ms = [int]$serializationStopwatch.ElapsedMilliseconds
      dispose_ms = [int]$disposeStopwatch.ElapsedMilliseconds
    }
  }
}

function SafeSegment([string]$Text) {
  if ([string]::IsNullOrWhiteSpace($Text)) {
    return "unknown"
  }
  $clean = [System.Text.RegularExpressions.Regex]::Replace($Text, "[^A-Za-z0-9_.-]", "-")
  $clean = $clean.Trim("-")
  if ([string]::IsNullOrWhiteSpace($clean)) {
    return "unknown"
  }
  if ($clean.Length -gt 64) {
    return $clean.Substring(0, 64)
  }
  return $clean
}

function FileSha256([string]$Path) {
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

$script:LegacyBitmapProcessorLoaded = $false
$script:LegacyBitmapProcessorCompileCount = 0
$script:LegacyBitmapProcessorCompileMs = 0

function EnsureLegacyBitmapProcessor() {
  if ($script:LegacyBitmapProcessorLoaded) {
    return
  }
  $compileStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $source = @"
using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

namespace ScreenRecognitionOcr
{
    public sealed class LegacyBitmapProcessingResult
    {
        public int Width { get; set; }
        public int Height { get; set; }
        public int PixelCount { get; set; }
        public int BytesPerPixel { get; set; }
        public int Stride { get; set; }
        public string PixelFormat { get; set; }
        public int GetPixelEquivalentCount { get; set; }
        public int SetPixelEquivalentCount { get; set; }
        public int LockBitsCount { get; set; }
        public int UnlockBitsCount { get; set; }
        public long LockBitsMs { get; set; }
        public long CopyInMs { get; set; }
        public long HistogramMs { get; set; }
        public long PixelTransformMs { get; set; }
        public long CopyOutMs { get; set; }
        public long UnlockBitsMs { get; set; }
    }

    public static class LegacyBitmapProcessorV1
    {
        public static LegacyBitmapProcessingResult Process(
            Bitmap bitmap,
            bool autocontrast,
            bool hasThreshold,
            int threshold,
            bool invert)
        {
            if (bitmap == null) throw new ArgumentNullException("bitmap");
            if (bitmap.Width <= 0 || bitmap.Height <= 0) throw new ArgumentException("Bitmap must be non-empty.");

            PixelFormat format = bitmap.PixelFormat;
            int bpp = Image.GetPixelFormatSize(format) / 8;
            if (format != PixelFormat.Format24bppRgb &&
                format != PixelFormat.Format32bppArgb &&
                format != PixelFormat.Format32bppPArgb &&
                format != PixelFormat.Format32bppRgb)
            {
                throw new NotSupportedException("Unsupported pixel format for legacy LockBits processing: " + format);
            }

            Rectangle rect = new Rectangle(0, 0, bitmap.Width, bitmap.Height);
            BitmapData data = null;
            byte[] buffer = null;
            int absStride = 0;
            LegacyBitmapProcessingResult result = new LegacyBitmapProcessingResult();
            result.Width = bitmap.Width;
            result.Height = bitmap.Height;
            result.PixelCount = bitmap.Width * bitmap.Height;
            result.BytesPerPixel = bpp;
            result.PixelFormat = format.ToString();
            result.GetPixelEquivalentCount = result.PixelCount + (autocontrast ? result.PixelCount : 0);
            result.SetPixelEquivalentCount = result.PixelCount;

            try
            {
                Stopwatch sw = Stopwatch.StartNew();
                data = bitmap.LockBits(rect, ImageLockMode.ReadWrite, format);
                sw.Stop();
                result.LockBitsMs = sw.ElapsedMilliseconds;
                result.LockBitsCount = 1;
                result.Stride = data.Stride;
                absStride = Math.Abs(data.Stride);
                buffer = new byte[absStride * bitmap.Height];

                sw.Restart();
                Marshal.Copy(data.Scan0, buffer, 0, buffer.Length);
                sw.Stop();
                result.CopyInMs = sw.ElapsedMilliseconds;

                int minGray = 255;
                int maxGray = 0;
                if (autocontrast)
                {
                    sw.Restart();
                    for (int y = 0; y < bitmap.Height; y++)
                    {
                        int row = y * absStride;
                        for (int x = 0; x < bitmap.Width; x++)
                        {
                            int offset = row + (x * bpp);
                            int gray = LegacyGray(buffer, offset, format);
                            if (gray < minGray) minGray = gray;
                            if (gray > maxGray) maxGray = gray;
                        }
                    }
                    sw.Stop();
                    result.HistogramMs = sw.ElapsedMilliseconds;
                }

                sw.Restart();
                for (int y = 0; y < bitmap.Height; y++)
                {
                    int row = y * absStride;
                    for (int x = 0; x < bitmap.Width; x++)
                    {
                        int offset = row + (x * bpp);
                        int gray = LegacyGray(buffer, offset, format);
                        if (autocontrast && maxGray > minGray)
                        {
                            gray = (int)Math.Round(((gray - minGray) * 255.0) / (maxGray - minGray));
                        }
                        if (hasThreshold)
                        {
                            gray = gray >= threshold ? 255 : 0;
                        }
                        if (invert)
                        {
                            gray = 255 - gray;
                        }
                        buffer[offset] = (byte)gray;
                        buffer[offset + 1] = (byte)gray;
                        buffer[offset + 2] = (byte)gray;
                        if (bpp == 4)
                        {
                            buffer[offset + 3] = 255;
                        }
                    }
                }
                sw.Stop();
                result.PixelTransformMs = sw.ElapsedMilliseconds;

                sw.Restart();
                Marshal.Copy(buffer, 0, data.Scan0, buffer.Length);
                sw.Stop();
                result.CopyOutMs = sw.ElapsedMilliseconds;
            }
            finally
            {
                if (data != null)
                {
                    Stopwatch unlock = Stopwatch.StartNew();
                    bitmap.UnlockBits(data);
                    unlock.Stop();
                    result.UnlockBitsMs = unlock.ElapsedMilliseconds;
                    result.UnlockBitsCount = 1;
                }
            }
            return result;
        }

        private static int LegacyGray(byte[] buffer, int offset, PixelFormat format)
        {
            int b = buffer[offset];
            int g = buffer[offset + 1];
            int r = buffer[offset + 2];
            if (format == PixelFormat.Format32bppPArgb)
            {
                int a = buffer[offset + 3];
                if (a == 0)
                {
                    r = 0;
                    g = 0;
                    b = 0;
                }
                else if (a < 255)
                {
                    r = Math.Min(255, ((r * 255) + (a / 2)) / a);
                    g = Math.Min(255, ((g * 255) + (a / 2)) / a);
                    b = Math.Min(255, ((b * 255) + (a / 2)) / a);
                }
            }
            return (int)Math.Round((r * 0.299) + (g * 0.587) + (b * 0.114));
        }
    }
}
"@
  Add-Type -TypeDefinition $source -ReferencedAssemblies @("System.Drawing.dll") -Language CSharp
  $compileStopwatch.Stop()
  $script:LegacyBitmapProcessorLoaded = $true
  $script:LegacyBitmapProcessorCompileCount += 1
  $script:LegacyBitmapProcessorCompileMs += [int]$compileStopwatch.ElapsedMilliseconds
}

function GetPreprocessingVariants($Payload) {
  $variants = @()
  if ($Payload.preprocessing -and $Payload.preprocessing.variants) {
    foreach ($variant in $Payload.preprocessing.variants) {
      $variants += $variant
    }
  }
  if ($variants.Count -eq 0) {
    $variants += [pscustomobject]@{
      name = "gray_3x"
      scale_factor = 3
      grayscale = $true
      autocontrast = $false
      sharpen = $false
      binary_threshold = $null
      invert = $false
    }
  }
  return @($variants | Select-Object -First 5)
}

function New-LegacyPreparedBitmapWithTiming($Source, [int]$X, [int]$Y, [int]$Width, [int]$Height, $Variant, [string]$PixelImplementation) {
  $scale = [int]$Variant.scale_factor
  if ($scale -lt 1) { $scale = 1 }
  $targetWidth = [Math]::Max(1, $Width * $scale)
  $targetHeight = [Math]::Max(1, $Height * $scale)
  $maxPixels = 2000000
  if ($targetWidth * $targetHeight -gt $maxPixels) {
    $ratio = [Math]::Sqrt($maxPixels / ($targetWidth * $targetHeight))
    $targetWidth = [Math]::Max(1, [int][Math]::Floor($targetWidth * $ratio))
    $targetHeight = [Math]::Max(1, [int][Math]::Floor($targetHeight * $ratio))
  }
  $drawStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $crop = New-Object System.Drawing.Bitmap $targetWidth, $targetHeight
  $graphics = [System.Drawing.Graphics]::FromImage($crop)
  $graphics.Clear([System.Drawing.Color]::White)
  $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
  $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  $destRect = New-Object System.Drawing.Rectangle 0, 0, $targetWidth, $targetHeight
  $srcRect = New-Object System.Drawing.Rectangle $X, $Y, $Width, $Height
  $graphics.DrawImage($Source, $destRect, $srcRect, [System.Drawing.GraphicsUnit]::Pixel)
  $graphics.Dispose()
  $drawStopwatch.Stop()
  $pixelTiming = ApplyPixelPreprocessing $crop $Variant $PixelImplementation
  $pixelTiming.draw_resize_ms = [int]$drawStopwatch.ElapsedMilliseconds
  return @{
    bitmap = $crop
    timing = $pixelTiming
  }
}

function New-LegacyPreparedBitmap($Source, [int]$X, [int]$Y, [int]$Width, [int]$Height, $Variant) {
  $prepared = New-LegacyPreparedBitmapWithTiming $Source $X $Y $Width $Height $Variant "legacy-pixel-loop"
  return $prepared.bitmap
}

function New-LegacyPreparedBitmapFast($Source, [int]$X, [int]$Y, [int]$Width, [int]$Height, $Variant) {
  $prepared = New-LegacyPreparedBitmapWithTiming $Source $X $Y $Width $Height $Variant "lockbits-v1"
  return $prepared.bitmap
}

function EmptyPixelTiming([string]$Implementation) {
  return @{
    pixel_implementation = $Implementation
    draw_resize_ms = 0
    pixel_read_ms = 0
    histogram_ms = 0
    grayscale_ms = 0
    autocontrast_ms = 0
    threshold_ms = 0
    invert_ms = 0
    pixel_write_ms = 0
    pixel_transform_ms = 0
    lockbits_ms = 0
    unlockbits_ms = 0
    marshal_copy_in_ms = 0
    marshal_copy_out_ms = 0
    encode_ms = 0
    get_pixel_call_count = 0
    set_pixel_call_count = 0
    lockbits_count = 0
    unlockbits_count = 0
    pixel_count = 0
    bitmap_pixel_format = $null
    stride = 0
    bytes_per_pixel = 0
  }
}

function ApplyPixelPreprocessingLockBits($Bitmap, $Variant) {
  EnsureLegacyBitmapProcessor
  $threshold = 0
  $hasThreshold = $false
  if ($null -ne $Variant.binary_threshold) {
    $threshold = [int]$Variant.binary_threshold
    $hasThreshold = $true
  }
  $result = [ScreenRecognitionOcr.LegacyBitmapProcessorV1]::Process(
    $Bitmap,
    [bool]$Variant.autocontrast,
    $hasThreshold,
    $threshold,
    [bool]$Variant.invert
  )
  return @{
    pixel_implementation = "lockbits-v1"
    draw_resize_ms = 0
    pixel_read_ms = [int]$result.CopyInMs
    histogram_ms = [int]$result.HistogramMs
    grayscale_ms = [int]$result.PixelTransformMs
    autocontrast_ms = if ([bool]$Variant.autocontrast) { [int]$result.HistogramMs } else { 0 }
    threshold_ms = if ($hasThreshold) { [int]$result.PixelTransformMs } else { 0 }
    invert_ms = if ([bool]$Variant.invert) { [int]$result.PixelTransformMs } else { 0 }
    pixel_write_ms = [int]$result.CopyOutMs
    pixel_transform_ms = [int]$result.PixelTransformMs
    lockbits_ms = [int]$result.LockBitsMs
    unlockbits_ms = [int]$result.UnlockBitsMs
    marshal_copy_in_ms = [int]$result.CopyInMs
    marshal_copy_out_ms = [int]$result.CopyOutMs
    encode_ms = 0
    get_pixel_call_count = [int]$result.GetPixelEquivalentCount
    set_pixel_call_count = [int]$result.SetPixelEquivalentCount
    lockbits_count = [int]$result.LockBitsCount
    unlockbits_count = [int]$result.UnlockBitsCount
    pixel_count = [int]$result.PixelCount
    bitmap_pixel_format = [string]$result.PixelFormat
    stride = [int]$result.Stride
    bytes_per_pixel = [int]$result.BytesPerPixel
  }
}

function ApplyPixelPreprocessing($Bitmap, $Variant, [string]$PixelImplementation = "legacy-pixel-loop") {
  $timing = EmptyPixelTiming $PixelImplementation
  $timing.pixel_count = [int]($Bitmap.Width * $Bitmap.Height)
  $timing.bitmap_pixel_format = [string]$Bitmap.PixelFormat
  $needsPixels = [bool]$Variant.autocontrast -or $null -ne $Variant.binary_threshold -or [bool]$Variant.invert
  if (-not $needsPixels) {
    return $timing
  }
  if ($PixelImplementation -eq "lockbits-v1") {
    return ApplyPixelPreprocessingLockBits $Bitmap $Variant
  }
  if ($PixelImplementation -ne "legacy-pixel-loop") {
    throw "Unknown System.Drawing pixel implementation: $PixelImplementation"
  }
  $threshold = $null
  if ($null -ne $Variant.binary_threshold) {
    $threshold = [int]$Variant.binary_threshold
  }
  $minGray = 255
  $maxGray = 0
  $pixelCount = [int]($Bitmap.Width * $Bitmap.Height)
  if ([bool]$Variant.autocontrast) {
    $histogramStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    for ($py = 0; $py -lt $Bitmap.Height; $py++) {
      for ($px = 0; $px -lt $Bitmap.Width; $px++) {
        $color = $Bitmap.GetPixel($px, $py)
        $gray = [int](($color.R * 0.299) + ($color.G * 0.587) + ($color.B * 0.114))
        if ($gray -lt $minGray) { $minGray = $gray }
        if ($gray -gt $maxGray) { $maxGray = $gray }
      }
    }
    $histogramStopwatch.Stop()
    $timing.histogram_ms = [int]$histogramStopwatch.ElapsedMilliseconds
    $timing.pixel_read_ms += [int]$histogramStopwatch.ElapsedMilliseconds
  }
  $transformStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  for ($py = 0; $py -lt $Bitmap.Height; $py++) {
    for ($px = 0; $px -lt $Bitmap.Width; $px++) {
      $color = $Bitmap.GetPixel($px, $py)
      $gray = [int](($color.R * 0.299) + ($color.G * 0.587) + ($color.B * 0.114))
      if ([bool]$Variant.autocontrast -and $maxGray -gt $minGray) {
        $gray = [int][Math]::Round((($gray - $minGray) * 255.0) / ($maxGray - $minGray))
      }
      if ($null -ne $threshold) {
        if ($gray -ge $threshold) { $gray = 255 } else { $gray = 0 }
      }
      if ([bool]$Variant.invert) {
        $gray = 255 - $gray
      }
      $Bitmap.SetPixel($px, $py, [System.Drawing.Color]::FromArgb($gray, $gray, $gray))
    }
  }
  $transformStopwatch.Stop()
  $timing.grayscale_ms = [int]$transformStopwatch.ElapsedMilliseconds
  $timing.autocontrast_ms = if ([bool]$Variant.autocontrast) { [int]$timing.histogram_ms } else { 0 }
  $timing.threshold_ms = if ($null -ne $threshold) { [int]$transformStopwatch.ElapsedMilliseconds } else { 0 }
  $timing.invert_ms = if ([bool]$Variant.invert) { [int]$transformStopwatch.ElapsedMilliseconds } else { 0 }
  $timing.pixel_transform_ms = [int]$transformStopwatch.ElapsedMilliseconds
  $timing.pixel_write_ms = [int]$transformStopwatch.ElapsedMilliseconds
  $extraReads = 0
  if ([bool]$Variant.autocontrast) { $extraReads = $pixelCount }
  $timing.get_pixel_call_count = $pixelCount + $extraReads
  $timing.set_pixel_call_count = $pixelCount
  return $timing
}

function NewCropBitmap($Source, [int]$X, [int]$Y, [int]$Width, [int]$Height, $Variant) {
  return New-LegacyPreparedBitmap $Source $X $Y $Width $Height $Variant
}

function RegionHasInk($Bitmap, [int]$X, [int]$Y, [int]$Width, [int]$Height) {
  $stepX = [Math]::Max(1, [int][Math]::Floor($Width / 32))
  $stepY = [Math]::Max(1, [int][Math]::Floor($Height / 32))
  $right = [Math]::Min($Bitmap.Width, $X + $Width)
  $bottom = [Math]::Min($Bitmap.Height, $Y + $Height)
  for ($py = $Y; $py -lt $bottom; $py += $stepY) {
    for ($px = $X; $px -lt $right; $px += $stepX) {
      $color = $Bitmap.GetPixel($px, $py)
      if ($color.R -lt 245 -or $color.G -lt 245 -or $color.B -lt 245) {
        return $true
      }
    }
  }
  return $false
}

function ScoreRecognizedText([string]$FieldName, [string]$Text) {
  $clean = if ($Text) { $Text.Trim() } else { "" }
  if ($clean.Length -eq 0) {
    return 0
  }
  $score = 10
  if ($FieldName -match "bid|ask|price|levels") {
    if ($clean -match "\d+[\.,]\d{1,2}") { $score += 40 }
    elseif ($clean -match "\d+") { $score += 20 }
    if ($clean -match "[A-Za-z\u4e00-\u9fff]") { $score -= 5 }
  } elseif ($FieldName -match "quantity") {
    if ($clean -match "\b\d+\b") { $score += 30 }
    if ($clean -match "\d+[\.,]\d+") { $score -= 15 }
  } elseif ($clean.Length -gt 2) {
    $score += 20
  }
  return $score
}

$inputPayload = Get-Content -Raw -Encoding UTF8 $InputJson | ConvertFrom-Json

if ([string]$inputPayload.schema_version -eq "windows-ocr-legacy-prepared-export-v1") {
  $exportDiagnostics = @{
    mode = "legacy_prepared_export"
    export_enabled = $true
    ocr_invocation_count = 0
    helper_total_duration_ms = 0
  }
  $exports = New-Object System.Collections.Generic.List[object]
  $exportWarnings = New-Object System.Collections.Generic.List[string]
  $imagePath = [string]$inputPayload.image_path
  $outputDir = [string]$inputPayload.output_dir
  if ([string]::IsNullOrWhiteSpace($outputDir)) {
    throw "legacy prepared export requires output_dir."
  }
  [System.IO.Directory]::CreateDirectory($outputDir) | Out-Null
  $source = [System.Drawing.Bitmap]::FromFile($imagePath)
  $preprocessingVariants = GetPreprocessingVariants $inputPayload
  $requestIdentity = SafeSegment ([string]$inputPayload.request_identity)
  try {
    foreach ($property in $inputPayload.rois.PSObject.Properties) {
      $fieldName = [string]$property.Name
      $roi = $property.Value
      $x = [Math]::Floor([decimal]$roi.x * $source.Width)
      $y = [Math]::Floor([decimal]$roi.y * $source.Height)
      $w = [Math]::Max(1, [Math]::Floor([decimal]$roi.width * $source.Width))
      $h = [Math]::Max(1, [Math]::Floor([decimal]$roi.height * $source.Height))
      if ($x + $w -gt $source.Width) { $w = $source.Width - $x }
      if ($y + $h -gt $source.Height) { $h = $source.Height - $y }
      if (-not (RegionHasInk $source $x $y $w $h)) {
        $exportWarnings.Add("blank_roi:" + $fieldName)
        continue
      }
      foreach ($variant in $preprocessingVariants) {
        $variantName = [string]$variant.name
        if ([string]::IsNullOrWhiteSpace($variantName)) { $variantName = "unnamed" }
        $crop = NewCropBitmap $source $x $y $w $h $variant
        $temporaryName = [System.Guid]::NewGuid().ToString("N") + ".png"
        $temporaryPath = Join-Path $outputDir $temporaryName
        $crop.Save($temporaryPath, [System.Drawing.Imaging.ImageFormat]::Png)
        $cropWidth = $crop.Width
        $cropHeight = $crop.Height
        $crop.Dispose()
        $hash = FileSha256 $temporaryPath
        $targetName = (SafeSegment $requestIdentity) + "." + (SafeSegment $fieldName) + "." + (SafeSegment $variantName) + "." + $hash.Substring(0, 16) + ".png"
        $targetPath = Join-Path $outputDir $targetName
        if (Test-Path -LiteralPath $targetPath) {
          Remove-Item -LiteralPath $temporaryPath -Force
        } else {
          Move-Item -LiteralPath $temporaryPath -Destination $targetPath
        }
        $exports.Add(@{
          request_id = ($requestIdentity + ":" + $fieldName + ":" + $variantName)
          field_name = $fieldName
          pipeline_name = $variantName
          image_path = $targetPath
          width = [int]$cropWidth
          height = [int]$cropHeight
          format = "PNG"
          sha256 = $hash
          source = "legacy"
        })
      }
    }
  }
  finally {
    $source.Dispose()
    $helperStopwatch.Stop()
    $exportDiagnostics.helper_total_duration_ms = [int]$helperStopwatch.ElapsedMilliseconds
  }
  @{
    schema_version = "windows-ocr-legacy-prepared-export-v1"
    exports = @($exports.ToArray())
    warnings = @($exportWarnings | Select-Object -Unique)
    diagnostics = $exportDiagnostics
  } | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $OutputJson
  exit 0
}

if ([string]$inputPayload.schema_version -eq "windows-ocr-prepared-only-v1") {
  $consumerMode = [string]$inputPayload.consumer_mode
  if ($consumerMode -ne "legacy" -and $consumerMode -ne "batch") {
    throw "prepared-only consumer_mode must be legacy or batch."
  }
  $preparedDiagnostics = @{
    mode = ("prepared_only_" + $consumerMode)
    crop_count = 0
    resize_count = 0
    preprocessing_count = 0
    ocr_invocation_count = 0
    actual_ocr_invocation_count = 0
    ocr_engine_initialization_count = 0
    ocr_engine_initialization_total_ms = 0
    total_ocr_duration_ms = 0
    request_error_count = 0
    helper_total_duration_ms = 0
    ocr_language_source = "user_profile_languages"
  }
  $preparedWarnings = New-Object System.Collections.Generic.List[string]
  $preparedWarnings.Add("ocr_confidence_unavailable")
  $preparedResults = New-Object System.Collections.Generic.List[object]
  $engine = $null
  try {
    if ($consumerMode -eq "batch") {
      $engineInfo = NewOcrEngine
      $engine = $engineInfo.engine
      $preparedDiagnostics.ocr_engine_initialization_count = 1
      $preparedDiagnostics.ocr_engine_initialization_total_ms = [int]$engineInfo.timing
    }
    foreach ($request in $inputPayload.requests) {
      $requestId = [string]$request.request_id
      $path = [string]$request.image_path
      try {
        if ($consumerMode -eq "batch") {
          $recognized = RecognizePreparedImage $path $engine
          $recognizedTiming = $recognized.timing
        } else {
          $recognized = RecognizeImage $path
          $preparedDiagnostics.ocr_engine_initialization_count += 1
          $preparedDiagnostics.ocr_engine_initialization_total_ms += [int]$recognized.timing.engine_initialization_ms
          $recognizedTiming = @{
            total_ms = [int]$recognized.timing.total_ms
            image_open_ms = 0
            bitmap_decode_ms = 0
            recognize_ms = [int]$recognized.timing.ocr_execution_ms
            serialization_ms = 0
            dispose_ms = 0
          }
        }
        $preparedDiagnostics.ocr_invocation_count += 1
        $preparedDiagnostics.actual_ocr_invocation_count += 1
        $preparedDiagnostics.total_ocr_duration_ms += [int]$recognized.timing.total_ms
        $preparedResults.Add(@{
          request_id = $requestId
          raw_text = [string]$recognized.text
          lines = @($recognized.lines)
          timing = $recognizedTiming
          decoder = $recognized.decoder
          error_code = $null
        })
      }
      catch {
        $preparedDiagnostics.request_error_count += 1
        $preparedResults.Add(@{
          request_id = $requestId
          raw_text = ""
          lines = @()
          timing = @{
            total_ms = 0
            image_open_ms = 0
            bitmap_decode_ms = 0
            recognize_ms = 0
            serialization_ms = 0
            dispose_ms = 0
          }
          decoder = $null
          error_code = "ocr_request_failed"
          error_message = ([string]$_.Exception.Message)
        })
      }
    }
  }
  finally {
    $helperStopwatch.Stop()
    $preparedDiagnostics.helper_total_duration_ms = [int]$helperStopwatch.ElapsedMilliseconds
  }
  @{
    schema_version = "windows-ocr-prepared-only-v1"
    consumer_mode = $consumerMode
    results = @($preparedResults.ToArray())
    warnings = @($preparedWarnings | Select-Object -Unique)
    diagnostics = $preparedDiagnostics
  } | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $OutputJson
  exit 0
}

if ([string]$inputPayload.schema_version -eq "windows-ocr-system-drawing-batch-v1") {
  $pixelImplementation = [string]$inputPayload.pixel_implementation
  if ([string]::IsNullOrWhiteSpace($pixelImplementation)) {
    $pixelImplementation = "lockbits-v1"
  }
  if ($pixelImplementation -ne "lockbits-v1" -and $pixelImplementation -ne "legacy-pixel-loop") {
    throw "Unknown System.Drawing pixel implementation: $pixelImplementation"
  }
  $systemDrawingDiagnostics = @{
    mode = "system_drawing_batch_v1"
    pixel_implementation = $pixelImplementation
    powershell_process_count = 1
    source_image_open_count = 0
    graphics_creation_count = 0
    prepared_image_count = 0
    prepared_image_write_count = 0
    ocr_invocation_count = 0
    actual_ocr_invocation_count = 0
    ocr_engine_initialization_count = 0
    ocr_engine_initialization_total_ms = 0
    total_ocr_duration_ms = 0
    helper_total_duration_ms = 0
    request_error_count = 0
    blank_roi_count = 0
    skip_ocr = [bool]$inputPayload.skip_ocr
    csharp_type_compile_count = 0
    csharp_type_compile_ms = 0
    draw_resize_ms = 0
    pixel_read_ms = 0
    histogram_ms = 0
    grayscale_ms = 0
    autocontrast_ms = 0
    threshold_ms = 0
    invert_ms = 0
    pixel_write_ms = 0
    pixel_transform_ms = 0
    lockbits_ms = 0
    unlockbits_ms = 0
    marshal_copy_in_ms = 0
    marshal_copy_out_ms = 0
    encode_ms = 0
    get_pixel_call_count = 0
    set_pixel_call_count = 0
    lockbits_count = 0
    unlockbits_count = 0
  }
  $systemDrawingWarnings = New-Object System.Collections.Generic.List[string]
  $systemDrawingWarnings.Add("ocr_confidence_unavailable")
  $systemDrawingResults = New-Object System.Collections.Generic.List[object]
  $systemDrawingTempFiles = New-Object System.Collections.Generic.List[string]
  $source = $null
  $engine = $null
  try {
    $sourceImagePath = [string]$inputPayload.source_image_path
    if ([string]::IsNullOrWhiteSpace($sourceImagePath)) {
      throw "system drawing batch requires source_image_path."
    }
    $debugDir = $inputPayload.debug_artifacts_dir
    if ($debugDir) {
      [System.IO.Directory]::CreateDirectory([string]$debugDir) | Out-Null
    }
    $source = [System.Drawing.Bitmap]::FromFile($sourceImagePath)
    $systemDrawingDiagnostics.source_image_open_count = 1
    if (-not [bool]$inputPayload.skip_ocr -and $inputPayload.requests.Count -gt 0) {
      $engineInfo = NewOcrEngine
      $engine = $engineInfo.engine
      $systemDrawingDiagnostics.ocr_engine_initialization_count = 1
      $systemDrawingDiagnostics.ocr_engine_initialization_total_ms = [int]$engineInfo.timing
    }
    foreach ($request in $inputPayload.requests) {
      $requestId = [string]$request.request_id
      $fieldName = [string]$request.field_name
      $regionName = [string]$request.region
      $pipelineName = [string]$request.pipeline_name
      $descriptorHash = [string]$request.preprocessing_descriptor_hash
      $x = [int]$request.crop.x
      $y = [int]$request.crop.y
      $w = [int]$request.crop.width
      $h = [int]$request.crop.height
      try {
        if (-not (RegionHasInk $source $x $y $w $h)) {
          $systemDrawingDiagnostics.blank_roi_count += 1
          $systemDrawingResults.Add(@{
            request_id = $requestId
            field_name = $fieldName
            region = $regionName
            pipeline_name = $pipelineName
            preprocessing_descriptor_hash = $descriptorHash
            raw_text = ""
            lines = @()
            timing = @{
              total_ms = 0
              image_open_ms = 0
              bitmap_decode_ms = 0
              recognize_ms = 0
              serialization_ms = 0
              dispose_ms = 0
            }
            decoder = $null
            prepared = $null
            error_code = "blank_roi_fast_path"
          })
          continue
        }
        $preparedBitmap = New-LegacyPreparedBitmapWithTiming $source $x $y $w $h $request.preprocessing $pixelImplementation
        $crop = $preparedBitmap.bitmap
        $preprocessingTiming = $preparedBitmap.timing
        $systemDrawingDiagnostics.graphics_creation_count += 1
        $systemDrawingDiagnostics.prepared_image_count += 1
        $variantName = if ([string]::IsNullOrWhiteSpace($pipelineName)) { "unnamed" } else { $pipelineName }
        if ($debugDir) {
          $suffix = if ([string]::IsNullOrWhiteSpace($descriptorHash)) { [System.Guid]::NewGuid().ToString("N").Substring(0, 16) } else { $descriptorHash.Substring(0, [Math]::Min(16, $descriptorHash.Length)) }
          $preparedPath = Join-Path ([string]$debugDir) ((SafeSegment $fieldName) + "." + (SafeSegment $variantName) + "." + $suffix + ".png")
        } else {
          $preparedPath = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString("N") + ".png")
          $systemDrawingTempFiles.Add($preparedPath)
        }
        $encodeStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $crop.Save($preparedPath, [System.Drawing.Imaging.ImageFormat]::Png)
        $encodeStopwatch.Stop()
        $preprocessingTiming.encode_ms = [int]$encodeStopwatch.ElapsedMilliseconds
        foreach ($timingKey in @("draw_resize_ms", "pixel_read_ms", "histogram_ms", "grayscale_ms", "autocontrast_ms", "threshold_ms", "invert_ms", "pixel_write_ms", "pixel_transform_ms", "lockbits_ms", "unlockbits_ms", "marshal_copy_in_ms", "marshal_copy_out_ms", "encode_ms", "get_pixel_call_count", "set_pixel_call_count", "lockbits_count", "unlockbits_count")) {
          $systemDrawingDiagnostics[$timingKey] += [int]$preprocessingTiming[$timingKey]
        }
        $systemDrawingDiagnostics.prepared_image_write_count += 1
        $prepared = @{
          image_path = $preparedPath
          encoded_sha256 = FileSha256 $preparedPath
          width = [int]$crop.Width
          height = [int]$crop.Height
          pixel_format = [string]$crop.PixelFormat
          horizontal_resolution = [double]$crop.HorizontalResolution
          vertical_resolution = [double]$crop.VerticalResolution
          encoder = "PNG"
          preprocessing_timing = $preprocessingTiming
        }
        $crop.Dispose()
        if ([bool]$inputPayload.skip_ocr) {
          $recognized = @{
            text = ""
            lines = @()
            timing = @{
              total_ms = 0
              image_open_ms = 0
              bitmap_decode_ms = 0
              recognize_ms = 0
              serialization_ms = 0
              dispose_ms = 0
            }
            decoder = $null
          }
        } else {
          $recognized = RecognizePreparedImage $preparedPath $engine
          $systemDrawingDiagnostics.ocr_invocation_count += 1
          $systemDrawingDiagnostics.actual_ocr_invocation_count += 1
          $systemDrawingDiagnostics.total_ocr_duration_ms += [int]$recognized.timing.total_ms
        }
        $systemDrawingResults.Add(@{
          request_id = $requestId
          field_name = $fieldName
          region = $regionName
          pipeline_name = $pipelineName
          preprocessing_descriptor_hash = $descriptorHash
          raw_text = [string]$recognized.text
          lines = @($recognized.lines)
          timing = $recognized.timing
          decoder = $recognized.decoder
          prepared = $prepared
          preprocessing_timing = $preprocessingTiming
          error_code = $null
        })
      }
      catch {
        $systemDrawingDiagnostics.request_error_count += 1
        $systemDrawingResults.Add(@{
          request_id = $requestId
          field_name = $fieldName
          region = $regionName
          pipeline_name = $pipelineName
          preprocessing_descriptor_hash = $descriptorHash
          raw_text = ""
          lines = @()
          timing = @{
            total_ms = 0
            image_open_ms = 0
            bitmap_decode_ms = 0
            recognize_ms = 0
            serialization_ms = 0
            dispose_ms = 0
          }
          decoder = $null
          prepared = $null
          error_code = "ocr_request_failed"
          error_message = ([string]$_.Exception.Message)
        })
      }
    }
  }
  finally {
    if ($null -ne $source) { $source.Dispose() }
    foreach ($tempFile in $systemDrawingTempFiles) {
      if (Test-Path $tempFile) {
        Remove-Item -LiteralPath $tempFile -Force
      }
    }
    $helperStopwatch.Stop()
    $systemDrawingDiagnostics.helper_total_duration_ms = [int]$helperStopwatch.ElapsedMilliseconds
    $systemDrawingDiagnostics.csharp_type_compile_count = [int]$script:LegacyBitmapProcessorCompileCount
    $systemDrawingDiagnostics.csharp_type_compile_ms = [int]$script:LegacyBitmapProcessorCompileMs
  }
  @{
    schema_version = "windows-ocr-system-drawing-batch-v1"
    results = @($systemDrawingResults.ToArray())
    warnings = @($systemDrawingWarnings | Select-Object -Unique)
    diagnostics = $systemDrawingDiagnostics
  } | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 $OutputJson
  exit 0
}

if ([string]$inputPayload.schema_version -eq "windows-ocr-batch-v1") {
  $batchDiagnostics = @{
    mode = "batch_v1"
    powershell_process_count = 1
    ocr_invocation_count = 0
    actual_ocr_invocation_count = 0
    ocr_engine_initialization_count = 0
    ocr_engine_initialization_total_ms = 0
    total_ocr_duration_ms = 0
    helper_total_duration_ms = 0
    request_error_count = 0
  }
  $batchWarnings = New-Object System.Collections.Generic.List[string]
  $batchWarnings.Add("ocr_confidence_unavailable")
  $batchResults = New-Object System.Collections.Generic.List[object]
  try {
    $engineInfo = NewOcrEngine
    $engine = $engineInfo.engine
    $batchDiagnostics.ocr_engine_initialization_count = 1
    $batchDiagnostics.ocr_engine_initialization_total_ms = [int]$engineInfo.timing
    foreach ($request in $inputPayload.requests) {
      $requestId = [string]$request.request_id
      try {
        $recognized = RecognizePreparedImage ([string]$request.image_path) $engine
        $batchDiagnostics.ocr_invocation_count += 1
        $batchDiagnostics.actual_ocr_invocation_count += 1
        $batchDiagnostics.total_ocr_duration_ms += [int]$recognized.timing.total_ms
        $batchResults.Add(@{
          request_id = $requestId
          raw_text = [string]$recognized.text
          lines = @($recognized.lines)
          timing = $recognized.timing
          error_code = $null
        })
      }
      catch {
        $batchDiagnostics.request_error_count += 1
        $batchResults.Add(@{
          request_id = $requestId
          raw_text = ""
          lines = @()
          timing = @{
            total_ms = 0
            image_open_ms = 0
            bitmap_decode_ms = 0
            recognize_ms = 0
            serialization_ms = 0
            dispose_ms = 0
          }
          error_code = "ocr_request_failed"
          error_message = ([string]$_.Exception.Message)
        })
      }
    }
  }
  finally {
    $helperStopwatch.Stop()
    $batchDiagnostics.helper_total_duration_ms = [int]$helperStopwatch.ElapsedMilliseconds
  }
  $batchOutput = @{
    schema_version = "windows-ocr-batch-v1"
    results = @($batchResults.ToArray())
    warnings = @($batchWarnings | Select-Object -Unique)
    diagnostics = $batchDiagnostics
  }
  $batchOutput | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $OutputJson
  exit 0
}

$imagePath = [string]$inputPayload.image_path
$debugDir = $inputPayload.debug_artifacts_dir
$source = [System.Drawing.Bitmap]::FromFile($imagePath)
$fields = @{}
$warnings = New-Object System.Collections.Generic.List[string]
$warnings.Add("ocr_confidence_unavailable")
$tempFiles = New-Object System.Collections.Generic.List[string]
$preprocessingVariants = GetPreprocessingVariants $inputPayload
$diagnostics = @{
  powershell_process_count = 1
  ocr_invocation_count = 0
  pipeline_count_attempted = 0
  pipeline_count_completed = 0
  early_exit_used = $false
  blank_roi_count = 0
  per_pipeline_duration_ms = @()
  fields = @{}
  total_ocr_duration_ms = 0
}

try {
  foreach ($property in $inputPayload.rois.PSObject.Properties) {
    $fieldStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $fieldName = $property.Name
    $roi = $property.Value
    $diagnostics.fields[$fieldName] = @{
      blank_roi_fast_path = $false
      pipeline_count_attempted = 0
      pipeline_count_completed = 0
      selected_pipeline = $null
      duration_ms = 0
    }
    $x = [Math]::Floor([decimal]$roi.x * $source.Width)
    $y = [Math]::Floor([decimal]$roi.y * $source.Height)
    $w = [Math]::Max(1, [Math]::Floor([decimal]$roi.width * $source.Width))
    $h = [Math]::Max(1, [Math]::Floor([decimal]$roi.height * $source.Height))
    if ($x + $w -gt $source.Width) { $w = $source.Width - $x }
    if ($y + $h -gt $source.Height) { $h = $source.Height - $y }

    if (-not (RegionHasInk $source $x $y $w $h)) {
      $fieldStopwatch.Stop()
      $diagnostics.blank_roi_count += 1
      $diagnostics.fields[$fieldName].blank_roi_fast_path = $true
      $diagnostics.fields[$fieldName].duration_ms = [int]$fieldStopwatch.ElapsedMilliseconds
      $fields[$fieldName] = @{
        raw_text = ""
        confidence = $null
        confidence_source = "unavailable"
        bounding_box = @{
          x = "0"
          y = "0"
          width = [string][decimal]$w
          height = [string][decimal]$h
        }
        lines = @()
        warnings = @("ocr_confidence_unavailable", "preprocessing_pipeline:blank_roi_fast_path")
      }
      continue
    }

    $best = $null
    $bestScore = -9999
    foreach ($variant in $preprocessingVariants) {
      $pipelineStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
      $diagnostics.pipeline_count_attempted += 1
      $diagnostics.fields[$fieldName].pipeline_count_attempted += 1
      $crop = NewCropBitmap $source $x $y $w $h $variant
      $variantName = [string]$variant.name
      if ([string]::IsNullOrWhiteSpace($variantName)) { $variantName = "unnamed" }
      if ($debugDir) {
        [System.IO.Directory]::CreateDirectory([string]$debugDir) | Out-Null
        $cropPath = Join-Path ([string]$debugDir) ($fieldName + "." + $variantName + ".png")
      } else {
        $cropPath = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString("N") + ".png")
        $tempFiles.Add($cropPath)
      }
      $crop.Save($cropPath, [System.Drawing.Imaging.ImageFormat]::Png)
      $cropWidth = $crop.Width
      $cropHeight = $crop.Height
      $crop.Dispose()
      $recognized = RecognizeImage $cropPath
      $diagnostics.ocr_invocation_count += 1
      $diagnostics.total_ocr_duration_ms += [int]$recognized.timing.total_ms
      $score = ScoreRecognizedText $fieldName $recognized.text
      $pipelineStopwatch.Stop()
      $diagnostics.pipeline_count_completed += 1
      $diagnostics.fields[$fieldName].pipeline_count_completed += 1
      $diagnostics.per_pipeline_duration_ms += @{
        field_name = $fieldName
        pipeline_name = $variantName
        duration_ms = [int]$pipelineStopwatch.ElapsedMilliseconds
        ocr_total_ms = [int]$recognized.timing.total_ms
        engine_initialization_ms = [int]$recognized.timing.engine_initialization_ms
        ocr_execution_ms = [int]$recognized.timing.ocr_execution_ms
        produced_text = -not [string]::IsNullOrWhiteSpace([string]$recognized.text)
        selected = $false
      }
      if ($score -gt $bestScore) {
        $bestScore = $score
        $best = @{
          recognized = $recognized
          pipeline_name = $variantName
          width = $cropWidth
          height = $cropHeight
        }
      }
    }
    if ($null -eq $best) {
      throw "No OCR preprocessing pipeline produced a result."
    }
    foreach ($pipelineTiming in $diagnostics.per_pipeline_duration_ms) {
      if ($pipelineTiming.field_name -eq $fieldName -and $pipelineTiming.pipeline_name -eq $best.pipeline_name) {
        $pipelineTiming.selected = $true
      }
    }
    $fieldStopwatch.Stop()
    $diagnostics.fields[$fieldName].selected_pipeline = $best.pipeline_name
    $diagnostics.fields[$fieldName].duration_ms = [int]$fieldStopwatch.ElapsedMilliseconds
    $fields[$fieldName] = @{
      raw_text = $best.recognized.text
      confidence = $null
      confidence_source = "unavailable"
      bounding_box = @{
        x = "0"
        y = "0"
        width = [string][decimal]($best.width)
        height = [string][decimal]($best.height)
      }
      lines = @($best.recognized.lines)
      warnings = @("ocr_confidence_unavailable", ("preprocessing_pipeline:" + $best.pipeline_name))
    }
  }
}
finally {
  $source.Dispose()
  foreach ($tempFile in $tempFiles) {
    if (Test-Path $tempFile) {
      Remove-Item -LiteralPath $tempFile -Force
    }
  }
}

$helperStopwatch.Stop()
$diagnostics.helper_total_duration_ms = [int]$helperStopwatch.ElapsedMilliseconds
$output = @{
  fields = $fields
  warnings = @($warnings | Select-Object -Unique)
  diagnostics = $diagnostics
}
$output | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $OutputJson
