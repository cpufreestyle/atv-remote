#!/bin/zsh
# ATV Remote APK 手工构建（无需 Gradle / Android Studio）
# 依赖: Android SDK build-tools 35 + platforms/android-35 + homebrew openjdk@17
set -e
cd "$(dirname "$0")"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ---- 版本号单一来源：根目录 VERSION ----
VERSION_NAME=$(sed -n 's/^versionName=//p' "$ROOT/VERSION")
VERSION_CODE=$(sed -n 's/^versionCode=//p' "$ROOT/VERSION")

# ---- SDK：环境变量优先，其次 macOS 默认位置 ----
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
if [ ! -d "$SDK" ]; then
    echo "找不到 Android SDK（$SDK）。设 ANDROID_HOME / ANDROID_SDK_ROOT，或装到 ~/Library/Android/sdk"
    exit 1
fi

# ---- build-tools：优先 35.0.0，没有就用已安装的最高版 ----
BT="${SDK}/build-tools/35.0.0"
[ -x "$BT/aapt2" ] || BT=$(ls -d "${SDK}"/build-tools/*/ 2>/dev/null | sort -V | tail -1)
if [ ! -x "$BT/aapt2" ]; then
    echo "SDK 里没有可用的 build-tools"
    exit 1
fi

# ---- 编译平台：优先 android-35，没有就用已安装的最高版 ----
PLATFORM="${SDK}/platforms/android-35/android.jar"
[ -f "$PLATFORM" ] || PLATFORM=$(ls "${SDK}"/platforms/android-*/android.jar 2>/dev/null | sort -V | tail -1)
if [ ! -f "$PLATFORM" ]; then
    echo "SDK 里没有可用的 platform（需要 android-35 或更高）"
    exit 1
fi

# ---- JDK 17：JAVA_HOME 优先，其次系统 java_home，最后 homebrew openjdk@17 ----
if [ -z "${JAVA_HOME:-}" ] || [ ! -x "$JAVA_HOME/bin/java" ]; then
    JAVA_HOME=$(/usr/libexec/java_home -v 17 2>/dev/null || true)
fi
if [ -z "${JAVA_HOME:-}" ] || [ ! -x "$JAVA_HOME/bin/java" ]; then
    JAVA_HOME=$(brew --prefix openjdk@17 2>/dev/null || true)
fi
if [ -z "$JAVA_HOME" ] || [ ! -x "$JAVA_HOME/bin/java" ]; then
    echo "找不到 JDK 17。设 JAVA_HOME，或 brew install openjdk@17"
    exit 1
fi
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
sed -e "s/android:versionCode=\"[0-9]*\"/android:versionCode=\"$VERSION_CODE\"/" \
    -e "s/android:versionName=\"[^\"]*\"/android:versionName=\"$VERSION_NAME\"/" \
    AndroidManifest.xml > "$OUT/gen/AndroidManifest.xml"
"$BT/aapt2" link \
    -o "$OUT/base.apk" \
    -I "$PLATFORM" \
    --manifest "$OUT/gen/AndroidManifest.xml" \
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
