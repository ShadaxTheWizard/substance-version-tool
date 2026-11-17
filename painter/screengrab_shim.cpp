// LD_PRELOAD shim: make Painter's eyedropper work under XWayland.
//
// The colour picker calls QScreen::grabWindow(0) to copy a screen and shows
// that copy full-screen to pick from. Under XWayland the X root window is
// empty, so the copy is black. This replaces that one call with a real
// screenshot taken by the Wayland compositor (through the painter-screengrab
// helper script). Grabs of specific windows are passed through untouched.
//
// Painter then shows one full-screen overlay window per screen. With several
// monitors the Wayland compositor does not reliably put each of those managed
// full-screen windows on its own monitor (KWin stacks two on one output, which
// flickers), so the overlays are turned into unmanaged windows that sit exactly
// where Painter places them: on the screen whose copy they show.
//
// The helper is asked for one screen by name. It returns that screen (KWin,
// niri) or the whole desktop (grim). Either is resampled to X11 pixels; from a
// desktop shot the part belonging to the grabbed screen is cut out. Several
// monitors with different scales therefore work.
//
// Build (no Qt libraries are linked; Painter's own are used at run time):
//   g++ -shared -fPIC -O2 -std=c++17 $(pkg-config --cflags Qt6Widgets)
//     -o libpainter-screengrab.so screengrab_shim.cpp -ldl
#include <QtGui/QGuiApplication>
#include <QtGui/QImage>
#include <QtGui/QImageReader>
#include <QtGui/QPixmap>
#include <QtGui/QScreen>
#include <QtWidgets/QWidget>

#include <dlfcn.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

extern char **environ;

namespace {

using Clock = std::chrono::steady_clock;
using OrigGrab = QPixmap (*)(QScreen *, WId, int, int, int, int);

// A pick grabs every screen in quick succession. The screenshots for all of
// them are taken up front and kept this long, so the picker's own overlays are
// never captured.
constexpr auto kShotLifetime = std::chrono::milliseconds(1500);

// Painter's child processes (crash handler, web engine) must not load the shim.
__attribute__((constructor)) void dropPreload() { unsetenv("LD_PRELOAD"); }

QImage takeScreenshot(const QScreen *screen)
{
    const char *helper = getenv("PAINTER_SCREENGRAB_HELPER");
    if (!helper || !*helper)
        return {};
    const char *runtime = getenv("XDG_RUNTIME_DIR");
    std::string path = std::string(runtime && *runtime ? runtime : "/tmp")
                       + "/painter-screengrab-" + std::to_string(getpid()) + ".png";
    const QByteArray name = screen->name().toLocal8Bit();
    char *argv[] = {const_cast<char *>(helper), const_cast<char *>(path.c_str()),
                    const_cast<char *>(name.constData()), nullptr};
    pid_t pid;
    if (posix_spawn(&pid, helper, nullptr, nullptr, argv, environ) != 0)
        return {};
    int status = 0;
    waitpid(pid, &status, 0);

    QImage image;
    if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
        // A shot of several monitors can exceed Qt's default 256 MB image limit.
        const int limit = QImageReader::allocationLimit();
        QImageReader::setAllocationLimit(0);
        QImageReader reader(QString::fromLocal8Bit(path.c_str()));
        reader.setDecideFormatFromContent(true);
        image = reader.read();
        QImageReader::setAllocationLimit(limit);
    }
    unlink(path.c_str());
    return image;
}

// A screen's rectangle in X11 pixels. Qt scales a screen's size but not its origin.
QRect nativeRect(const QScreen *screen)
{
    const QRect g = screen->geometry();
    return QRect(g.topLeft(), (QSizeF(g.size()) * screen->devicePixelRatio()).toSize());
}

bool sameAspect(const QSize &a, const QSize &b)
{
    const double x = double(a.width()) * b.height(), y = double(a.height()) * b.width();
    return std::abs(x - y) <= 0.01 * std::max(x, y);
}

struct Shot {
    QImage image;   // in X11 pixels
    QRect area;     // the part of the X root window it shows
};

std::mutex shotMutex;
std::vector<Shot> shots;
Clock::time_point shotsTaken;

// Returns the X11-pixel image of `screen`, or a null image on failure.
QImage screenImage(const QScreen *screen)
{
    const QRect wanted = nativeRect(screen);
    std::lock_guard<std::mutex> lock(shotMutex);
    if (Clock::now() - shotsTaken > kShotLifetime)
        shots.clear();
    for (const Shot &shot : shots) {
        if (shot.area.contains(wanted))
            return shot.image.copy(wanted.translated(-shot.area.topLeft()));
    }

    const QImage fresh = takeScreenshot(screen);
    if (fresh.isNull())
        return {};
    QRect root;
    for (const QScreen *s : screen->virtualSiblings())
        root |= nativeRect(s);
    // The screen that was asked for, unless the shape only fits the desktop.
    const bool desktop = !sameAspect(fresh.size(), wanted.size())
                         && sameAspect(fresh.size(), root.size());
    Shot shot;
    shot.area = desktop ? root : wanted;
    // Nearest-neighbour: an eyedropper wants the colours that are on screen,
    // not blends of neighbouring pixels.
    shot.image = fresh.size() == shot.area.size()
        ? fresh
        : fresh.scaled(shot.area.size(), Qt::IgnoreAspectRatio, Qt::FastTransformation);
    if (shots.empty()) {
        shotsTaken = Clock::now();
        // Do not hold on to screen-sized images once the pick is over.
        std::thread([taken = shotsTaken] {
            std::this_thread::sleep_for(kShotLifetime + std::chrono::milliseconds(500));
            std::lock_guard<std::mutex> lock(shotMutex);
            if (shotsTaken == taken)
                shots.clear();
        }).detach();
    }
    shots.push_back(shot);
    return shot.image.copy(wanted.translated(-shot.area.topLeft()));
}

// Window flags of Painter's per-screen overlay (a QLabel): Dialog, frameless,
// stays on top, no drop shadow.
constexpr unsigned kOverlayFlags = 0x40040803u;
constexpr unsigned kUnmanaged = Qt::X11BypassWindowManagerHint;

bool onWayland() { return getenv("WAYLAND_DISPLAY") != nullptr; }

} // namespace

// The two functions below replace Qt functions that Painter calls; they are
// declared under their mangled names because they are not being redefined as
// members. Qt's flag types are passed as a plain integer.
extern "C" {
void shimLabelCtor(void *self, QWidget *parent, unsigned flags)
    asm("_ZN6QLabelC2EP7QWidget6QFlagsIN2Qt10WindowTypeEE");
void shimSetWindowState(QWidget *self, unsigned state)
    asm("_ZN7QWidget14setWindowStateE6QFlagsIN2Qt11WindowStateEE");
}

// QLabel::QLabel(QWidget *, Qt::WindowFlags): make the overlays unmanaged.
void shimLabelCtor(void *self, QWidget *parent, unsigned flags)
{
    using Ctor = void (*)(void *, QWidget *, unsigned);
    static const Ctor original = reinterpret_cast<Ctor>(
        dlsym(RTLD_NEXT, "_ZN6QLabelC2EP7QWidget6QFlagsIN2Qt10WindowTypeEE"));
    if (flags == kOverlayFlags && onWayland()) {
        flags |= kUnmanaged;
        // Painter shows each overlay before it grabs that screen. Take all the
        // screenshots now, so that no overlay can be in them.
        for (const QScreen *screen : QGuiApplication::screens())
            screenImage(screen);
    }
    original(self, parent, flags);
}

// QWidget::setWindowState: an unmanaged window cannot be full-screen; Painter
// sizes the overlay to its screen right after.
void shimSetWindowState(QWidget *self, unsigned state)
{
    using Set = void (*)(QWidget *, unsigned);
    static const Set original = reinterpret_cast<Set>(
        dlsym(RTLD_NEXT, "_ZN7QWidget14setWindowStateE6QFlagsIN2Qt11WindowStateEE"));
    if (unsigned(self->windowFlags().toInt()) == (kOverlayFlags | kUnmanaged))
        state &= ~unsigned(Qt::WindowFullScreen);
    original(self, state);
}

QPixmap QScreen::grabWindow(WId window, int x, int y, int width, int height)
{
    static const OrigGrab original =
        reinterpret_cast<OrigGrab>(dlsym(RTLD_NEXT, "_ZN7QScreen10grabWindowEyiiii"));
    if (window != 0 || !onWayland())
        return original(this, window, x, y, width, height);

    const QImage full = screenImage(this);
    if (full.isNull()) {
        fprintf(stderr, "painter-screengrab: screenshot failed, using the X11 grab\n");
        return original(this, window, x, y, width, height);
    }

    // Arguments are relative to the screen, in Qt's scaled coordinates.
    const qreal dpr = devicePixelRatio();
    const int nx = qRound(x * dpr), ny = qRound(y * dpr);
    const int nw = width < 0 ? full.width() - nx : qRound(width * dpr);
    const int nh = height < 0 ? full.height() - ny : qRound(height * dpr);
    QPixmap result = QPixmap::fromImage(full.copy(QRect(nx, ny, nw, nh)));
    result.setDevicePixelRatio(dpr);
    return result;
}
