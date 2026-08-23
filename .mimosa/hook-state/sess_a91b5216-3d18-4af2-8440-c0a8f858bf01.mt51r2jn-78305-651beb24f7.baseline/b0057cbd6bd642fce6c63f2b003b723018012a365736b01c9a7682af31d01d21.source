#!/bin/zsh
# ATV Remote APK 手工构建（无需 Gradle / Android Studio）
# 依赖: Android SDK build-tools 35 + platforms/android-35 + homebrew openjdk@17
set -e
cd "$(dirname "$0")"

SDK="$HOME/Library/Android/sdk"
BT="$SDK/build-tools/35.0.0"
PLATFORM="$SDK/platforms/android-35/android.jar"
JAVA_HOME="$(brew --prefix openjdk@17)"
export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"
JAVA="$JAVA_HOME/bin/java"
JAVAC="$JAVA_HOME/bin/javac"
KEYTOOL="$JAVA_HOME/bin/keytool"

OUT=build
rm -rf "$OUT" && mkdir -p "$OUT/gen" "$OUT/classes" "$OUT/dex" "$OUT/apk"

echo "[1/7] aapt2 compile 资源"
"$BT/aapt2" compile --dir res -o "$OUT/res.zip"

echo "[2/7] aapt2 link 生成基础 APK + R.java"
"$BT/aapt2" link \
    -o "$OUT/base.apk" \
    -I "$PLATFORM" \
    --manifest AndroidManifest.xml \
    --java "$OUT/gen" \
    --auto-add-overlay \
    "$OUT/res.zip"

echo "[3/7] javac 编译 Java"
"$JAVAC" -source 8 -target 8 -nowarn \
    -classpath "$PLATFORM" \
    -d "$OUT/classes" \
    $(find "$OUT/gen" java -name '*.java')

echo "[4/7] d8 转 dex"
"$JAVA" -cp "$BT/lib/d8.jar" com.android.tools.r8.D8 \
    --release --lib "$PLATFORM" --min-api 26 \
    --output "$OUT/dex" \
    $(find "$OUT/classes" -name '*.class')

echo "[5/7] 组装 APK"
# 注意：resources.arsc 必须保持未压缩存储（Android 11+ 要求），
# 因此在 aapt2 产物上直接追加 classes.dex，不重新打包资源
cp "$OUT/base.apk" "$OUT/ATVRemote-unsigned.apk"
(cd "$OUT/dex" && zip -q "../ATVRemote-unsigned.apk" classes.dex)

echo "[6/7] zipalign"
"$BT/zipalign" -f -p 4 "$OUT/ATVRemote-unsigned.apk" "$OUT/ATVRemote-aligned.apk"

echo "[7/7] 签名"
KS=debug.keystore
if [ ! -f "$KS" ]; then
    "$KEYTOOL" -genkeypair -keystore "$KS" -alias atvremote \
        -storepass atvremote -keypass atvremote -keyalg RSA -keysize 2048 \
        -validity 10000 -dname "CN=ATV Remote, O=Home, C=CN" 2>/dev/null
fi
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:atvremote \
    --out ATVRemote.apk "$OUT/ATVRemote-aligned.apk"
"$BT/apksigner" verify ATVRemote.apk && echo ""
ls -lh ATVRemote.apk
echo "✅ 构建完成: $(pwd)/ATVRemote.apk"
