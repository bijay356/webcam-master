$ErrorActionPreference = "Stop"

$SDK = "C:\Users\admin\AppData\Local\Android\Sdk"
$BUILD_TOOLS = "$SDK\build-tools\36.0.0"
$PLATFORM_JAR = "$SDK\platforms\android-36\android.jar"
$JAVA_BIN = "C:\Program Files\Microsoft\jdk-17.0.19.10-hotspot\bin"
$ADB = "$SDK\platform-tools\adb.exe"

$ROOT = "f:\pc software\MotoMicNative"
$OUT = "$ROOT\build"
if (Test-Path $OUT) { Remove-Item -Recurse -Force $OUT }
New-Item -ItemType Directory -Path "$OUT\classes", "$OUT\src_gen" | Out-Null

Write-Host "0. Compiling resources with aapt2..."
& "$BUILD_TOOLS\aapt2.exe" compile --dir "$ROOT\res" -o "$OUT\res.zip"

Write-Host "1. Linking AndroidManifest & resources with aapt2..."
& "$BUILD_TOOLS\aapt2.exe" link -o "$OUT\unaligned.apk" --manifest "$ROOT\AndroidManifest.xml" -I $PLATFORM_JAR -R "$OUT\res.zip" --java "$OUT\src_gen"

Write-Host "2. Compiling Java sources with javac..."
& "$JAVA_BIN\javac.exe" -source 11 -target 11 -encoding UTF-8 -cp $PLATFORM_JAR -d "$OUT\classes" "$OUT\src_gen\com\example\motomic\R.java" "$ROOT\src\com\example\motomic\MainActivity.java" "$ROOT\src\com\example\motomic\MotoMicService.java"

Write-Host "3. Converting class files to DEX with d8..."
$classFiles = Get-ChildItem -Recurse -Filter "*.class" "$OUT\classes" | ForEach-Object { $_.FullName }
& "$BUILD_TOOLS\d8.bat" --lib $PLATFORM_JAR --output $OUT $classFiles

Write-Host "4. Adding classes.dex to APK..."
& "$JAVA_BIN\jar.exe" -uf "$OUT\unaligned.apk" -C "$OUT" classes.dex

Write-Host "5. Aligning APK with zipalign..."
& "$BUILD_TOOLS\zipalign.exe" -f -p 4 "$OUT\unaligned.apk" "$OUT\aligned.apk"

Write-Host "6. Generating Debug Keystore & Signing APK..."
$KEYSTORE = "$ROOT\debug.keystore"
if (-not (Test-Path $KEYSTORE)) {
    & "$JAVA_BIN\keytool.exe" -genkeypair -v -keystore $KEYSTORE -storepass android -alias androiddebugkey -keypass android -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Android Debug,O=Android,C=US"
}
$FINAL_APK = "f:\pc software\WebcamMaster.apk"
& "$BUILD_TOOLS\apksigner.bat" sign --ks $KEYSTORE --ks-pass pass:android --key-pass pass:android --out $FINAL_APK "$OUT\aligned.apk"
Copy-Item -Path $FINAL_APK -Destination "f:\pc software\MotoMicPro.apk" -Force

Write-Host "SUCCESS: Built signed APK at $FINAL_APK"
