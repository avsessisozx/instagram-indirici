#!/usr/bin/env bash
# Emülatörde uçtan uca testleri çalıştırır; ekran görüntülerini ve günlükleri ci-out/ klasörüne toplar.
# GitHub Actions'taki emülatör adımı tarafından çağrılır (depo kök klasöründen).
set -u
mkdir -p ci-out
adb logcat -c || true
adb shell rm -rf /sdcard/Download/ii-screens || true

(cd android && ./gradlew --no-daemon --stacktrace connectedDebugAndroidTest) > ci-out/e2e-gradle.log 2>&1
code=$?

adb pull /sdcard/Download/ii-screens ci-out/ || true
adb logcat -d -v time > ci-out/logcat-full.txt 2>/dev/null || true
grep -E "instagramindirici|chromium|WebView|AndroidRuntime|TestRunner|System.err" ci-out/logcat-full.txt | tail -n 4000 > ci-out/logcat.txt || true
rm -f ci-out/logcat-full.txt
tail -n 300 ci-out/e2e-gradle.log > ci-out/e2e-gradle-tail.log || true
echo "gradle exit code: $code" > ci-out/result.txt
exit $code
