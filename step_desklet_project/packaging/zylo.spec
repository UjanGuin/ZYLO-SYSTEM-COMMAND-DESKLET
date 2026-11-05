Name:           zylo-desktop-assistant
Version:        1.0.0
Release:        1%{?dist}
Summary:        GNOME desktop assistant launcher and extension
License:        MIT
URL:            https://github.com/UjanGuin/ZYLO-SYSTEM-COMMAND-DESKLET
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
Requires:       python3
Requires:       gnome-shell

%description
ZYLO is a GNOME Shell desktop assistant for Fedora Workstation. The package
installs a launcher and application sources. The first launcher run installs
the Python runtime and GNOME Shell extension for the current user, then opens
the setup form for local provider keys and accent color.

%prep
%autosetup -n %{name}-%{version}

%build
:

%install
mkdir -p %{buildroot}%{_datadir}/%{name}
cp -a extension_bridge.py gnome_extension install-extension.sh install.sh \
    requirements.txt setup_app.py step-desklet step_desklet assets \
    %{buildroot}%{_datadir}/%{name}/
install -D -m 0755 packaging/zylo-launcher \
    %{buildroot}%{_bindir}/zylo-desktop-assistant
install -D -m 0644 packaging/zylo.desktop \
    %{buildroot}%{_datadir}/applications/zylo.desktop

%files
%license LICENSE
%doc README.md
%{_bindir}/zylo-desktop-assistant
%{_datadir}/applications/zylo.desktop
%{_datadir}/%{name}/

%changelog
* Thu Nov 05 2026 Swapnanil Guin <129659296+UjanGuin@users.noreply.github.com> - 1.0.0-1
- Package the Fedora GNOME launcher, extension sources, and setup runtime.
