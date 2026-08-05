#
# Circle to Search — RPM spec (Fedora 41+, tested against Fedora 44 / Plasma 6).
#
# Local build:
#     sudo dnf install rpm-build rpmdevtools python3-devel
#     rpmdev-setuptree
#     git archive --format=tar.gz --prefix=circle-to-search-1.1.0/ \
#         -o ~/rpmbuild/SOURCES/circle-to-search-1.1.0.tar.gz HEAD
#     rpmbuild -ba circle-to-search.spec
#
# COPR (see docs/TECHNICAL.md for the click-by-click version):
#     dnf install copr-cli && copr-cli create circle-to-search --chroot fedora-44-x86_64
#     rpmbuild -bs circle-to-search.spec
#     copr-cli build circle-to-search ~/rpmbuild/SRPMS/circle-to-search-1.1.0-1.*.src.rpm
#
%global appid    io.github.fand1l.CircleToSearch
%global scriptid circletosearch
%global appdir   %{_datadir}/circle-to-search

Name:           circle-to-search
Version:        1.1.0
Release:        1%{?dist}
Summary:        Shake the cursor to search a screen region with Google Lens
Summary(uk):    Потрясіть курсором, щоб знайти ділянку екрана через Google Lens

License:        MIT
URL:            https://github.com/fand1l/cts_gl
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib

Requires:       python3-pyqt6
Requires:       python3-pillow
Requires:       python3-requests
Requires:       kf6-kpackage
Requires:       kf6-kconfig-core
Requires:       kwin-wayland
Requires:       xdg-utils
# Only needed when the ScreenShot2 permission check refuses us; it is part of
# every Plasma install anyway.
Recommends:     spectacle
# Optional, off by default: the T key in the overlay reads the text out of the
# selection locally instead of searching for the image.  Everything works
# without it.
Suggests:       tesseract

%description
Circle to Search freezes the screen when you shake the pointer diagonally, lets
you drag a rectangle over anything on it, and opens the Google Lens results for
that region in your browser.

It consists of a KWin script (the only place on Wayland where the global cursor
position is visible) and a small PyQt6 daemon that captures the screen through
org.kde.KWin.ScreenShot2, shows the selection overlay and uploads the crop.
There is no OCR, no model and no API key involved.

%description -l uk
Circle to Search «заморожує» екран, коли ви трясете курсором по діагоналі,
дає виділити прямокутник і відкриває результати Google Lens для цієї ділянки
у браузері.

Складається з KWin-скрипта (єдине місце у Wayland, звідки видно глобальну
позицію курсора) і невеликого демона на PyQt6, який знімає екран через
org.kde.KWin.ScreenShot2, показує оверлей виділення та вивантажує виріз.

%prep
%autosetup -n %{name}-%{version}

%build
# Nothing to build: pure Python plus a JavaScript KWin script.

%install
# Python package
install -d %{buildroot}%{appdir}
cp -a src/circle_to_search %{buildroot}%{appdir}/

# Launcher
install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/%{name} <<EOF
#!/bin/sh
exec %{__python3} %{appdir}/circle_to_search/__main__.py "\$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/%{name}

# KWin script package
install -d %{buildroot}%{_datadir}/kwin/scripts/%{scriptid}
cp -a kwinscript/* %{buildroot}%{_datadir}/kwin/scripts/%{scriptid}/

# Desktop entry.  KWin checks ScreenShot2 callers by comparing /proc/<pid>/exe
# with the first token of Exec=, and /proc/<pid>/exe resolves the python3
# symlink — so the interpreter path is resolved here at build time.
install -d %{buildroot}%{_datadir}/applications
%global realpython %(readlink -f %{__python3})
sed -e 's|@PYTHON@|%{realpython}|g' -e 's|@APPDIR@|%{appdir}|g' \
    data/%{appid}.desktop.in > %{buildroot}%{_datadir}/applications/%{appid}.desktop

# Icon
install -d %{buildroot}%{_datadir}/icons/hicolor/scalable/apps
install -m 0644 data/icons/hicolor/scalable/apps/%{appid}.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/

# systemd --user unit
install -d %{buildroot}%{_userunitdir}
sed -e 's|@PYTHON@|%{realpython}|g' -e 's|@APPDIR@|%{appdir}|g' \
    data/%{name}.service.in > %{buildroot}%{_userunitdir}/%{name}.service

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{appid}.desktop

%post
%systemd_user_post %{name}.service
cat <<'EOF'

Circle to Search is installed.  Two per-user steps are left (no root needed):

    kwriteconfig6 --file kwinrc --group Plugins --key circletosearchEnabled true
    qdbus6 org.kde.KWin /KWin reconfigure
    systemctl --user enable --now circle-to-search.service

EOF

%preun
%systemd_user_preun %{name}.service

%postun
%systemd_user_postun_with_restart %{name}.service

%files
%license LICENSE
%doc README.md README.uk.md docs/TECHNICAL.md
%{_bindir}/%{name}
%{appdir}/
%{_datadir}/kwin/scripts/%{scriptid}/
%{_datadir}/applications/%{appid}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{appid}.svg
%{_userunitdir}/%{name}.service

%changelog
* Wed Aug 05 2026 fand1l <maximus171007@gmail.com> - 1.1.0-1
- "screenshot-window": search selected text instead of a picture of it, say
  what will actually be sent, take the same area as last time, black out part
  of a selection before it leaves, pick a colour off the screen, pin a crop to
  the screen, and a searchable window for the kept captures.

* Mon Aug 03 2026 fand1l <maximus171007@gmail.com> - 1.0.0-1
- Initial package: KWin shake detection, ScreenShot2/Spectacle/portal capture,
  selection overlay and Google Lens upload.
