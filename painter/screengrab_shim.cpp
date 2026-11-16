// LD_PRELOAD shim: make Painter's eyedropper work under XWayland.
//
// The colour picker calls QScreen::grabWindow(0) to copy the whole screen and
// shows that copy full-screen to pick from. Under XWayland the X root window is
// empty, so the copy is black. This replaces that one call with a real
// screenshot taken by the Wayland compositor (through the painter-screengrab
// helper script). Grabs of specific windows are passed through untouched.
//
// Build (no Qt libraries are linked; Painter's own are used at run time):
//   g++ -shared -fPIC -O2 -std=c++17 $(pkg-config --cflags Qt6Gui)
//     -o libpainter-screengrab.so screengrab_shim.cpp -ldl
#include <QtGui/QPixmap>
#include <QtGui/QScreen>

#include <dlfcn.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

extern char **environ;

namespace {

using Clock = std::chrono::steady_clock;
using OrigGrab = QPixmap (*)(QScreen *, WId, int, int, int, int);

// Painter's child processes (crash handler, web engine) must not load the shim.
__attribute__((constructor)) void dropPreload() { unsetenv("LD_PRELOAD"); }

std::vector<unsigned char> takeScreenshot()
{
    const char *helper = getenv("PAINTER_SCREENGRAB_HELPER");
    if (!helper || !*helper)
        return {};
    const char *runtime = getenv("XDG_RUNTIME_DIR");
    std::string path = std::string(runtime && *runtime ? runtime : "/tmp")
                       + "/painter-screengrab-" + std::to_string(getpid()) + ".png";
    char *argv[] = {const_cast<char *>(helper), const_cast<char *>(path.c_str()), nullptr};
    pid_t pid;
    if (posix_spawn(&pid, helper, nullptr, nullptr, argv, environ) != 0)
        return {};
    int status = 0;
    waitpid(pid, &status, 0);

    std::vector<unsigned char> data;
    if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
        if (FILE *f = fopen(path.c_str(), "rb")) {
            unsigned char buf[65536];
            size_t n;
            while ((n = fread(buf, 1, sizeof buf, f)) > 0)
                data.insert(data.end(), buf, buf + n);
            fclose(f);
        }
    }
    unlink(path.c_str());
    return data;
}

} // namespace

QPixmap QScreen::grabWindow(WId window, int x, int y, int width, int height)
{
    static const OrigGrab original =
        reinterpret_cast<OrigGrab>(dlsym(RTLD_NEXT, "_ZN7QScreen10grabWindowEyiiii"));
    if (window != 0 || !getenv("WAYLAND_DISPLAY"))
        return original(this, window, x, y, width, height);

    // The picker may grab more than once per pick; reuse a very recent shot so
    // the picker's own overlay is never captured.
    static QPixmap shot;
    static Clock::time_point taken;
    const auto now = Clock::now();
    if (shot.isNull() || now - taken > std::chrono::milliseconds(1500)) {
        const std::vector<unsigned char> png = takeScreenshot();
        QPixmap fresh;
        if (!png.empty())
            fresh.loadFromData(png.data(), static_cast<uint>(png.size()), "PNG");
        if (fresh.isNull()) {
            fprintf(stderr, "painter-screengrab: screenshot failed, using the X11 grab\n");
            return original(this, window, x, y, width, height);
        }
        shot = fresh;
        taken = Clock::now();
    }

    // Arguments are in Qt's scaled coordinates; the screenshot is in real pixels.
    const qreal dpr = devicePixelRatio();
    const QSize native = (QSizeF(geometry().size()) * dpr).toSize();
    QPixmap full = shot.size() == native
        ? shot : shot.scaled(native, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
    const int nx = qRound(x * dpr), ny = qRound(y * dpr);
    const int nw = width < 0 ? native.width() - nx : qRound(width * dpr);
    const int nh = height < 0 ? native.height() - ny : qRound(height * dpr);
    QPixmap result = full.copy(QRect(nx, ny, nw, nh));
    result.setDevicePixelRatio(dpr);
    return result;
}
