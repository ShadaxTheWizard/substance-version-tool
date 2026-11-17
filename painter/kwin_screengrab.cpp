// Screenshot of one KDE Plasma output, at its native resolution, through
// KWin's org.kde.KWin.ScreenShot2 D-Bus interface (used by the eyedropper fix on
// KDE, see painter-screengrab and screengrab_shim.cpp).
//
//   painter-kwin-screengrab OUT.ppm OUTPUT-NAME        (e.g. DP-1)
//
// KWin only serves this interface to programs whose .desktop file lists it in
// X-KDE-DBUS-Restricted-Interfaces and whose Exec is the calling executable;
// install.sh writes that file. Exit status: 0 on success, 1 otherwise.
//
// Build: g++ -O2 -std=c++17 -fPIC $(pkg-config --cflags --libs Qt6DBus Qt6Gui)
//          -o painter-kwin-screengrab kwin_screengrab.cpp
#include <QtCore/QCoreApplication>
#include <QtCore/QVariantMap>
#include <QtDBus/QDBusArgument>
#include <QtDBus/QDBusConnection>
#include <QtDBus/QDBusMessage>
#include <QtDBus/QDBusUnixFileDescriptor>
#include <QtGui/QImage>

#include <unistd.h>

#include <cstdio>

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    if (argc != 3) {
        fprintf(stderr, "usage: %s OUT.ppm OUTPUT-NAME\n", argv[0]);
        return 1;
    }

    int fds[2];
    if (pipe(fds) != 0)
        return 1;

    QDBusMessage call = QDBusMessage::createMethodCall(
        QStringLiteral("org.kde.KWin"), QStringLiteral("/org/kde/KWin/ScreenShot2"),
        QStringLiteral("org.kde.KWin.ScreenShot2"), QStringLiteral("CaptureScreen"));
    QVariantMap options;
    options[QStringLiteral("native-resolution")] = true;
    options[QStringLiteral("include-cursor")] = false;
    call << QString::fromLocal8Bit(argv[2]) << options
         << QVariant::fromValue(QDBusUnixFileDescriptor(fds[1]));
    const QDBusMessage reply = QDBusConnection::sessionBus().call(call, QDBus::Block, 5000);
    close(fds[1]);
    if (reply.type() != QDBusMessage::ReplyMessage || reply.arguments().isEmpty()) {
        fprintf(stderr, "painter-kwin-screengrab: %s\n", qPrintable(reply.errorMessage()));
        return 1;
    }

    const QVariantMap meta = qdbus_cast<QVariantMap>(reply.arguments().constFirst());
    const int width = meta.value(QStringLiteral("width")).toInt();
    const int height = meta.value(QStringLiteral("height")).toInt();
    const qsizetype stride = meta.value(QStringLiteral("stride")).toInt();
    const auto format = QImage::Format(meta.value(QStringLiteral("format")).toInt());
    if (meta.value(QStringLiteral("type")).toString() != QLatin1String("raw")
            || width <= 0 || height <= 0 || stride <= 0)
        return 1;

    QImage image(width, height, format);
    if (image.isNull() || image.bytesPerLine() < stride)
        return 1;
    for (int y = 0; y < height; ++y) {
        uchar *line = image.scanLine(y);
        for (qsizetype got = 0; got < stride;) {
            const ssize_t n = read(fds[0], line + got, size_t(stride - got));
            if (n <= 0)
                return 1;
            got += n;
        }
    }
    return image.save(QString::fromLocal8Bit(argv[1]), "PPM") ? 0 : 1;
}
